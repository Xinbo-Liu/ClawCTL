#!/usr/bin/env bash
# 用途：复用已由 root 侧物化并写出的 ingress 边界证据，避免非 root 用户无法读取宿主机防火墙语义时覆盖有效证据。

ingress_boundary_cache_env_value() {
  local env_file="$1"
  local key="$2"
  awk -F= -v key="$key" '
    $1 == key {
      value = substr($0, length(key) + 2)
      gsub(/^["'\'']|["'\'']$/, "", value)
      print value
      exit
    }
  ' "$env_file"
}

ingress_boundary_cache_abs_path() {
  local root_dir="$1"
  local value="$2"
  if [[ "$value" == /* ]]; then
    printf '%s\n' "$value"
  else
    printf '%s/%s\n' "$root_dir" "${value#./}"
  fi
}

ingress_boundary_cached_nginx_policy_ok() {
  local evidence_path="$1"
  local allowed_cidrs="$2"
  jq -e \
    --arg allowed_cidrs "$allowed_cidrs" \
    '
      def csv_set($raw):
        ($raw | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0)) | sort);
      .nginx_policy.required == true
      and .nginx_policy.checked == true
      and .nginx_policy.ok == true
      and .nginx_policy.default_deny == true
      and .nginx_policy.rewrite_phase_default_deny == true
      and .nginx_policy.access_phase_default_deny == true
      and ((.nginx_policy.source_cidrs // [] | sort) == csv_set($allowed_cidrs))
    ' "$evidence_path" >/dev/null
}

ingress_boundary_refresh_cached_nginx_policy() {
  local root_dir="$1"
  local env_file="$2"
  local evidence_path="$3"
  local evidence_dir=""
  local nginx_json=""
  local nginx_err=""
  local updated_json=""

  evidence_dir="$(dirname "$evidence_path")"
  [[ -w "$evidence_path" && -w "$evidence_dir" ]] || return 1
  nginx_json="$(mktemp)"
  nginx_err="$(mktemp)"
  updated_json="$(mktemp "$evidence_dir/.ingress-boundary-evidence.XXXXXX")"
  if ! bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" \
    setup ingress check-nginx \
    --env-file "$env_file" > "$nginx_json" 2> "$nginx_err" < /dev/null; then
    rm -f "$nginx_json" "$nginx_err" "$updated_json"
    return 1
  fi
  if ! jq --slurpfile nginx "$nginx_json" \
    '.nginx_policy = ({"required": true, "checked": true, "ok": true, "issues": []} + ($nginx[0] // {}))' \
    "$evidence_path" > "$updated_json"; then
    rm -f "$nginx_json" "$nginx_err" "$updated_json"
    return 1
  fi
  mv "$updated_json" "$evidence_path"
  chmod 600 "$evidence_path" 2>/dev/null || true
  rm -f "$nginx_json" "$nginx_err"
}

ingress_boundary_cached_evidence_ok() {
  local root_dir="$1"
  local env_file="$2"
  local require_nginx_policy="${3:-0}"
  local default_host_state_root="${4:-state/openclaw}"
  local host_state_root evidence_path listen_ip tls_cn allowed_cidrs

  command -v jq >/dev/null 2>&1 || return 1
  [[ -r "$env_file" ]] || return 1
  host_state_root="$(ingress_boundary_cache_env_value "$env_file" HOST_STATE_ROOT)"
  [[ -n "$host_state_root" ]] || host_state_root="$default_host_state_root"
  evidence_path="$(ingress_boundary_cache_abs_path "$root_dir" "$host_state_root")/control_plane/setup/ingress_boundary_evidence.json"
  [[ -r "$evidence_path" ]] || return 1
  listen_ip="$(ingress_boundary_cache_env_value "$env_file" OPENCLAW_INGRESS_LISTEN_IP)"
  tls_cn="$(ingress_boundary_cache_env_value "$env_file" OPENCLAW_TLS_CN)"
  allowed_cidrs="$(ingress_boundary_cache_env_value "$env_file" OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS)"
  if jq -e \
    --arg listen_ip "$listen_ip" \
    --arg tls_cn "$tls_cn" \
    --arg allowed_cidrs "$allowed_cidrs" \
    '
      def csv_set($raw):
        ($raw | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0)) | sort);
      .accepted == true
      and (.compose_contract.compose_contract_ok == true)
      and (.boundary_evidence.accepted == true)
      and ((.boundary_evidence.method // "") != "none")
      and (.boundary_evidence.expected_bind_ip == $listen_ip)
      and (.boundary_evidence.expected_tls_cn == $tls_cn)
      and ((.boundary_evidence.allowed_source_cidrs // [] | sort) == csv_set($allowed_cidrs))
    ' "$evidence_path" >/dev/null; then
    if [[ "$require_nginx_policy" != "1" ]]; then
      return 0
    fi
    if ingress_boundary_cached_nginx_policy_ok "$evidence_path" "$allowed_cidrs"; then
      return 0
    fi
    ingress_boundary_refresh_cached_nginx_policy "$root_dir" "$env_file" "$evidence_path" || return 1
    ingress_boundary_cached_nginx_policy_ok "$evidence_path" "$allowed_cidrs"
    return $?
  fi
  return 1
}
