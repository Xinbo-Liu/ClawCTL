#!/usr/bin/env bash
# 用途：集中收口 OpenClaw 运行态目录、权限与默认占位文件，避免 bootstrap/fix_permissions 重复维护。
set -euo pipefail
RUNTIME_PERMISSIONS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=repo_root_bootstrap.sh
source "$RUNTIME_PERMISSIONS_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$RUNTIME_PERMISSIONS_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
RUNTIME_PERMISSIONS_ROOT="$(openclaw_repo_root_from "$RUNTIME_PERMISSIONS_LIB_DIR")"
source "$RUNTIME_PERMISSIONS_LIB_DIR/host_install_defaults.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$RUNTIME_PERMISSIONS_ROOT/scripts/lib/repo_contracts.sh"
runtime_permissions_warn() { printf '[runtime_permissions] %s\n' "$*" >&2; }
runtime_permissions_chmod_if_exists() { local mode="$1"; local path="$2"; [[ -e "$path" ]] || return 0; chmod "$mode" "$path"; }
runtime_permissions_mkdir_chmod() { local mode="$1"; local path="$2"; mkdir -p "$path"; chmod "$mode" "$path"; }
runtime_permissions_touch_file() { local path="$1"; mkdir -p "$(dirname "$path")"; touch "$path"; }
runtime_permissions_touch_chmod() { local mode="$1"; local path="$2"; runtime_permissions_touch_file "$path"; chmod "$mode" "$path"; }
runtime_permissions_find_chmod_existing() {
  local mode="$1"
  shift
  find "$@" ! -perm "$mode" -exec sh -c '
mode="$1"
shift
status=0
for path do
  [ -e "$path" ] || [ -L "$path" ] || continue
  chmod "$mode" "$path" || status=$?
done
exit "$status"
' _ "$mode" {} +
}

runtime_permissions_abs_path() {
  local root="$1"
  local value="$2"
  local candidate=""
  [[ -n "$value" ]] || return 2
  if [[ "$value" == /* ]]; then
    candidate="$value"
  else
    candidate="$root/${value#./}"
  fi
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$candidate"
    return 0
  fi
  printf '%s\n' "$candidate"
}

runtime_permissions_host_state_root() {
  local root="$1"
  local default_state_root=""
  default_state_root="$(host_install_defaults_state_root_default)"
  local state_dir="${HOST_STATE_DIR:-${OPENCLAW_STATE_DIR:-$root/${default_state_root#./}}}"
  runtime_permissions_abs_path "$root" "$state_dir"
}

runtime_permissions_assert_access_mode() {
  local path="$1"
  local mode="$2"
  local label="$3"
  case "$mode" in
    r)
      [[ -r "$path" ]] || { runtime_permissions_warn "$label 不可读：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"; return 4; }
      ;;
    rx)
      [[ -r "$path" && -x "$path" ]] || { runtime_permissions_warn "$label 缺少读取/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"; return 4; }
      ;;
    rw)
      [[ -r "$path" && -w "$path" ]] || { runtime_permissions_warn "$label 缺少读取/写入权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"; return 4; }
      ;;
    rwx)
      [[ -r "$path" && -w "$path" && -x "$path" ]] || { runtime_permissions_warn "$label 缺少读取/写入/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"; return 4; }
      ;;
    *)
      runtime_permissions_warn "未知权限模式：$mode"
      return 4
      ;;
  esac
}

runtime_permissions_assert_dir_manageable_or_creatable() {
  local dir="$1"
  local label="$2"
  if [[ -d "$dir" ]]; then
    runtime_permissions_assert_access_mode "$dir" rwx "$label"
    return $?
  fi
  local parent
  parent="$(dirname "$dir")"
  [[ -d "$parent" ]] || { runtime_permissions_warn "$label 的父目录不存在：$parent；当前脚本不会自动越级补建目录。"; return 4; }
  runtime_permissions_assert_access_mode "$parent" rwx "$label 的父目录"
}

runtime_permissions_assert_file_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    [[ -f "$path" ]] || { runtime_permissions_warn "$label 不是常规文件：$path"; return 4; }
    runtime_permissions_assert_access_mode "$path" rw "$label"
    return $?
  fi
  local parent
  parent="$(dirname "$path")"
  [[ -d "$parent" ]] || { runtime_permissions_warn "$label 的父目录不存在：$parent；当前脚本不会自动越级补建目录。"; return 4; }
  runtime_permissions_assert_access_mode "$parent" rwx "$label 的父目录"
}

runtime_permissions_seed_json_if_missing() {
  local path="$1"
  local content="$2"
  [[ -f "$path" && -s "$path" ]] && return 0
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" >"$path"
}

runtime_permissions_prepare_repo_support_dirs() {
  local root="$1"
  local state_dir=""
  state_dir="$(runtime_permissions_host_state_root "$root")"
  runtime_permissions_mkdir_chmod 755 "$state_dir"
  runtime_permissions_mkdir_chmod 755 "$state_dir/logs"
  runtime_permissions_mkdir_chmod 755 "$state_dir/logs/nginx-gateway"
  runtime_permissions_mkdir_chmod 755 "$root/state/image_artifacts"
}

runtime_permissions_collect_manifest_host_bootstrap_entries() {
  local root="$1"
  local manifest="$root/$(repo_contract_relpath runtime.paths)"
  local state_dir=""
  local host_gateway_root=""
  local host_gateway_logs_root=""
  local host_control_plane_root=""
  local host_control_plane_setup_root=""
  local host_control_plane_dispatch_root=""
  state_dir="$(runtime_permissions_host_state_root "$root")"
  host_gateway_root="$state_dir/gateway"
  host_gateway_logs_root="$host_gateway_root/logs"
  host_control_plane_root="$state_dir/control_plane"
  host_control_plane_setup_root="$host_control_plane_root/setup"
  host_control_plane_dispatch_root="$host_control_plane_root/dispatch"
  [[ -f "$manifest" ]] || return 0
  awk \
    -v state_dir="$state_dir" \
    -v host_gateway_root="$host_gateway_root" \
    -v host_gateway_logs_root="$host_gateway_logs_root" \
    -v host_control_plane_root="$host_control_plane_root" \
    -v host_control_plane_setup_root="$host_control_plane_setup_root" \
    -v host_control_plane_dispatch_root="$host_control_plane_dispatch_root" '
    function render_host_path(value, rendered) {
      rendered = value
      gsub(/\{host_state_root\}/, state_dir, rendered)
      gsub(/\{host_gateway_root\}/, host_gateway_root, rendered)
      gsub(/\{host_gateway_logs_root\}/, host_gateway_logs_root, rendered)
      gsub(/\{host_control_plane_root\}/, host_control_plane_root, rendered)
      gsub(/\{host_control_plane_setup_root\}/, host_control_plane_setup_root, rendered)
      gsub(/\{host_control_plane_dispatch_root\}/, host_control_plane_dispatch_root, rendered)
      return rendered
    }
    BEGIN {
      in_entries = 0
      in_entry = 0
      in_paths = 0
      kind = ""
      create_dir = 0
      create_parent = 0
      host_path = ""
    }
    /^[[:space:]]*"entries"[[:space:]]*:[[:space:]]*[{]/ { in_entries = 1; next }
    !in_entries { next }
    !in_entry && /^[[:space:]]*"[^"]+"[[:space:]]*:[[:space:]]*[{]/ {
      in_entry = 1
      in_paths = 0
      kind = ""
      create_dir = 0
      create_parent = 0
      host_path = ""
      next
    }
    !in_entry { next }
    /^[[:space:]]*"kind"[[:space:]]*:[[:space:]]*"[^"]+"/ {
      line = $0
      sub(/^[[:space:]]*"kind"[[:space:]]*:[[:space:]]*"/, "", line)
      sub(/".*/, "", line)
      kind = line
      next
    }
    /^[[:space:]]*"create_on_bootstrap"[[:space:]]*:[[:space:]]*true/ { create_dir = 1; next }
    /^[[:space:]]*"create_parent_on_bootstrap"[[:space:]]*:[[:space:]]*true/ { create_parent = 1; next }
    /^[[:space:]]*"paths"[[:space:]]*:[[:space:]]*[{]/ { in_paths = 1; next }
    in_paths && /^[[:space:]]*"host"[[:space:]]*:[[:space:]]*"[^"]+"/ {
      line = $0
      sub(/^[[:space:]]*"host"[[:space:]]*:[[:space:]]*"/, "", line)
      sub(/".*/, "", line)
      host_path = render_host_path(line)
      next
    }
    in_paths && /^[[:space:]]*[}][[:space:]]*,?[[:space:]]*$/ { in_paths = 0; next }
    /^[[:space:]]*[}][[:space:]]*,?[[:space:]]*$/ {
      if (host_path != "") {
        if (create_dir == 1) {
          printf("dir\t%s\n", host_path)
        } else if (create_parent == 1) {
          sub(/\/[^/]+$/, "", host_path)
          if (host_path != "") {
            printf("parent\t%s\n", host_path)
          }
        }
      }
      in_entry = 0
      in_paths = 0
      next
    }
  ' "$manifest" | awk '!seen[$0]++'
}

runtime_permissions_render_compose_host_source() {
  local root="$1"
  local value="$2"
  local host_state_root="${HOST_STATE_ROOT:-$(host_install_defaults_state_root_default)}"
  local required_token='${HOST_STATE_ROOT:?HOST_STATE_ROOT_required}'
  local plain_token='${HOST_STATE_ROOT}'
  value="${value//$required_token/$host_state_root}"
  value="${value//$plain_token/$host_state_root}"
  printf '%s\n' "$value"
}

runtime_permissions_prepare_manifest_host_layout() {
  local root="$1"
  local action=""
  local path=""
  while IFS=$'\t' read -r action path; do
    [[ -n "$action" && -n "$path" ]] || continue
    case "$action" in
      dir|parent) runtime_permissions_mkdir_chmod 700 "$path" ;;
      *) runtime_permissions_warn "忽略未知 manifest 引导动作：$action -> $path" ;;
    esac
  done < <(runtime_permissions_collect_manifest_host_bootstrap_entries "$root")
}

runtime_permissions_collect_compose_host_bind_targets() {
  local root="$1"
  local compose="$root/deploy/docker-compose.yml"
  local compose_dir="$root/deploy"
  [[ -f "$compose" ]] || return 0
  awk '
    /^[[:space:]]*-[[:space:]]*\.[./]/ {
      line = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      host = ""
      depth = 0
      for (i = 1; i <= length(line); i++) {
        c = substr(line, i, 1)
        n = substr(line, i + 1, 1)
        if (c == "$" && n == "{") {
          depth += 1
          host = host c
          continue
        }
        if (c == "}" && depth > 0) {
          depth -= 1
          host = host c
          continue
        }
        if (c == ":" && depth == 0) {
          print host
          next
        }
        host = host c
      }
    }
  ' "$compose" | while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    rel="$(runtime_permissions_render_compose_host_source "$root" "$rel")"
    local abs=""
    abs="$(runtime_permissions_abs_path "$compose_dir" "$rel")"
    case "$abs" in
      "$root/state"/*|"$root/deploy/nginx/certs"*) ;;
      *) continue ;;
    esac
    if [[ "$abs" == "$root/deploy/nginx/certs" || "$abs" == "$root/deploy/nginx/certs/"* ]]; then
      printf 'dir\t%s\n' "$abs"
      continue
    fi
    case "$abs" in
      *.json|*.jsonl|*.env|*.conf|*.crt|*.key|*.pem|*.md|*.txt|*.log)
        printf 'parent\t%s\n' "$(dirname "$abs")"
        ;;
      *)
        printf 'dir\t%s\n' "$abs"
        ;;
    esac
  done | awk '!seen[$0]++'
}

runtime_permissions_align_repo_local_runtime_bind_owner() {
  local root="$1"
  local dir="$2"
  local uid_gid=""
  local uid=""
  local gid=""
  local abs_root=""
  local abs_dir=""
  [[ -d "$dir" ]] || return 0
  uid_gid="$(runtime_permissions_resolve_runtime_uid_gid "$root")"
  uid="${uid_gid%%:*}"
  gid="${uid_gid##*:}"
  [[ "$(id -u)" != "$uid" || "$(id -g)" != "$gid" ]] || return 0
  if [[ "$(id -u)" != '0' ]]; then
    runtime_permissions_warn "当前用户与 OPENCLAW_RUNTIME_UID/GID 不一致，且非 root；请先收口 repo-local runtime bind owner：$dir"
    return 0
  fi
  abs_root="$(runtime_permissions_abs_path "$root" "$root")"
  abs_dir="$(runtime_permissions_abs_path "$root" "$dir")"
  case "$abs_dir" in
    "$abs_root/deploy/nginx/certs"|"$abs_root/deploy/nginx/certs"/*) ;;
    *)
      runtime_permissions_warn "拒绝 chown 非 repo-local runtime bind 目录：$abs_dir"
      return 4
      ;;
  esac
  chown -R "$uid:$gid" "$abs_dir"
  runtime_permissions_align_openclaw_owner_only "$abs_dir"
}

runtime_permissions_prepare_compose_bind_layout() {
  local root="$1"
  local action=""
  local path=""
  while IFS=$'\t' read -r action path; do
    [[ -n "$action" && -n "$path" ]] || continue
    case "$action" in
      dir)
        if [[ "$path" == "$root/deploy/nginx/certs" || "$path" == "$root/deploy/nginx/certs/"* ]]; then
          runtime_permissions_mkdir_chmod 700 "$path"
        else
          runtime_permissions_mkdir_chmod 700 "$path"
        fi
        ;;
      parent)
        runtime_permissions_mkdir_chmod 700 "$path"
        ;;
      *) runtime_permissions_warn "忽略未知 compose 引导动作：$action -> $path" ;;
    esac
  done < <(runtime_permissions_collect_compose_host_bind_targets "$root")
  runtime_permissions_align_repo_local_runtime_bind_owner "$root" "$root/deploy/nginx/certs"
}

runtime_permissions_host_state_file() {
  local root="$1"
  local rel="$2"
  printf '%s/%s\n' "$(runtime_permissions_host_state_root "$root")" "${rel#./}"
}


runtime_permissions_host_gateway_state_dir() {
  local root="$1"
  printf '%s/gateway\n' "$(runtime_permissions_host_state_root "$root")"
}

runtime_permissions_host_control_plane_state_dir() {
  local root="$1"
  printf '%s/control_plane\n' "$(runtime_permissions_host_state_root "$root")"
}

runtime_permissions_host_gateway_file() {
  local root="$1"
  local rel="$2"
  printf '%s/%s\n' "$(runtime_permissions_host_gateway_state_dir "$root")" "${rel#./}"
}

runtime_permissions_host_control_plane_file() {
  local root="$1"
  local rel="$2"
  printf '%s/%s\n' "$(runtime_permissions_host_control_plane_state_dir "$root")" "${rel#./}"
}

runtime_permissions_prepare_control_plane_setup_layout() {
  local root="$1"
  local state_dir=""
  local control_plane_dir=""
  local setup_dir=""
  local host_setup_dir=""
  state_dir="$(runtime_permissions_host_state_root "$root")"
  control_plane_dir="$state_dir/control_plane"
  host_setup_dir="$state_dir/setup"
  setup_dir="$control_plane_dir/setup"
  runtime_permissions_mkdir_chmod 700 "$state_dir"
  runtime_permissions_mkdir_chmod 700 "$control_plane_dir"
  runtime_permissions_mkdir_chmod 700 "$control_plane_dir/tmp"
  runtime_permissions_mkdir_chmod 700 "$host_setup_dir"
  runtime_permissions_mkdir_chmod 700 "$setup_dir"
}

runtime_permissions_prepare_openclaw_state_layout() {
  local root="$1"
  local state_dir=""
  local gateway_dir=""
  local control_plane_dir=""
  local setup_dir=""
  state_dir="$(runtime_permissions_host_state_root "$root")"
  gateway_dir="$state_dir/gateway"
  control_plane_dir="$state_dir/control_plane"
  setup_dir="$control_plane_dir/setup"
  runtime_permissions_prepare_manifest_host_layout "$root"
  runtime_permissions_prepare_compose_bind_layout "$root"
  runtime_permissions_prepare_control_plane_setup_layout "$root"
  runtime_permissions_mkdir_chmod 700 "$gateway_dir"
  runtime_permissions_mkdir_chmod 700 "$setup_dir/official_cli"
  runtime_permissions_mkdir_chmod 700 "$gateway_dir/logs"
  runtime_permissions_mkdir_chmod 700 "$gateway_dir/logs/nginx-gateway"
  runtime_permissions_touch_file "$control_plane_dir/dispatch/.gitkeep"
  runtime_permissions_chmod_if_exists 600 "$control_plane_dir/deployment_acceptance.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/deployment_acceptance.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/one_click_test_full.latest.summary.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/one_click_test_full.latest.summary.md"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/one_click_deploy.latest.summary.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/one_click_deploy.latest.summary.md"
}

runtime_permissions_prepare_image_state_layout() {
  local root="$1"
  runtime_permissions_mkdir_chmod 700 "$root/state/image_artifacts"
  # state/image_pull 是镜像脚本运行时的临时状态目录，交付洁净度会把它视为可清理残留。
  # 权限修复只收口已存在的目录，不为 basic gate 预创建残留。
  if [[ -e "$root/state/image_pull" && ! -d "$root/state/image_pull" ]]; then
    runtime_permissions_warn "state/image_pull 应为目录但当前不是目录：$root/state/image_pull"
    return 4
  fi
  if [[ -d "$root/state/image_pull" ]]; then
    chmod 700 "$root/state/image_pull"
    runtime_permissions_chmod_if_exists 600 "$root/state/image_pull/cleanup_aliases.log"
    runtime_permissions_chmod_if_exists 600 "$root/state/image_pull/pull_records.log"
    runtime_permissions_chmod_if_exists 600 "$root/state/image_pull/pulled_images.txt"
  fi
}

runtime_permissions_prepare_release_evidence_layout() {
  local root="$1"
  local evidence_dir=""
  evidence_dir="$(runtime_permissions_host_control_plane_file "$root" release/evidence)"
  runtime_permissions_mkdir_chmod 700 "$(dirname "$evidence_dir")"
  runtime_permissions_mkdir_chmod 700 "$evidence_dir"
}

runtime_permissions_governed_release_paths() {
  local root="$1"
  local object_families=""
  object_families="$root/$(repo_contract_relpath control_plane.object_families)"
  [[ -f "$object_families" ]] || return 0
  jq -r '
    .families.runtime_evidence.entries[]?
    | select((.path_kind // "") == "host_control_plane_file")
    | (.path_ref // "")
    | strings
    | select(test("^release/evidence/[^.].*"))
    | select((test("(^|/)\\.\\.(/|$)") | not))
  ' "$object_families" | awk 'NF && !seen[$0]++'
}

runtime_permissions_harden_deploy_inputs() {
  local root="$1"
  runtime_permissions_chmod_if_exists 600 "$root/deploy/.env"
  runtime_permissions_chmod_if_exists 600 "$root/deploy/site.env"
  if [[ -d "$root/deploy/targets.d" ]]; then
    find "$root/deploy/targets.d" -maxdepth 1 -type f -name '*.env' -exec chmod 600 {} +
  fi
  if [[ -d "$root/agent/extensions" ]]; then
    find "$root/agent/extensions" -path '*/deploy/extension.env' -type f -exec chmod 600 {} +
  fi
}

runtime_permissions_deploy_env_value() {
  local root="$1"
  local key="$2"
  local env_file="$root/deploy/.env"
  [[ -f "$env_file" ]] || return 0
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
      }
    }
  ' "$env_file" | tail -n 1
}

runtime_permissions_validate_runtime_uid_gid_pair() {
  local uid="$1"
  local gid="$2"
  local source_label="$3"
  if [[ -z "$uid" && -z "$gid" ]]; then
    return 1
  fi
  if [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]]; then
    printf '%s:%s\n' "$uid" "$gid"
    return 0
  fi
  runtime_permissions_warn "$source_label 中的 OPENCLAW_RUNTIME_UID/GID 不完整或不是数字：${uid:-<empty>}:${gid:-<empty>}"
  return 4
}

runtime_permissions_running_runtime_uid_gid() {
  command -v docker >/dev/null 2>&1 || return 1
  local name=""
  local user_spec=""
  local uid=""
  local gid=""
  local resolved=""
  for name in openclaw-control-plane-scheduler openclaw-internal-api openclaw-official-gateway; do
    user_spec="$(docker inspect "$name" --format '{{.Config.User}}' 2>/dev/null || true)"
    [[ -n "$user_spec" ]] || continue
    uid="${user_spec%%:*}"
    gid="${user_spec##*:}"
    [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] || continue
    [[ "$uid" != '0' ]] || continue
    if [[ -n "$resolved" && "$resolved" != "$uid:$gid" ]]; then
      runtime_permissions_warn "正在运行的 runtime 容器用户不一致：$resolved 与 $name=$uid:$gid"
      return 4
    fi
    resolved="$uid:$gid"
  done
  [[ -n "$resolved" ]] || return 1
  runtime_permissions_warn "未从环境或 deploy/.env 读取到 OPENCLAW_RUNTIME_UID/GID，使用正在运行的 runtime 容器用户 $resolved 收口 host state owner。"
  printf '%s\n' "$resolved"
}

runtime_permissions_resolve_runtime_uid_gid() {
  local root="$1"
  local uid="${OPENCLAW_RUNTIME_UID:-}"
  local gid="${OPENCLAW_RUNTIME_GID:-}"
  local resolved=""
  local status=0
  resolved="$(runtime_permissions_validate_runtime_uid_gid_pair "$uid" "$gid" "当前环境" 2>/dev/null)" || status=$?
  if [[ "$status" == "0" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  if [[ "$status" == "4" ]]; then
    runtime_permissions_validate_runtime_uid_gid_pair "$uid" "$gid" "当前环境"
    return $?
  fi

  uid="$(runtime_permissions_deploy_env_value "$root" OPENCLAW_RUNTIME_UID)"
  gid="$(runtime_permissions_deploy_env_value "$root" OPENCLAW_RUNTIME_GID)"
  status=0
  resolved="$(runtime_permissions_validate_runtime_uid_gid_pair "$uid" "$gid" "$root/deploy/.env" 2>/dev/null)" || status=$?
  if [[ "$status" == "0" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  if [[ "$status" == "4" ]]; then
    runtime_permissions_validate_runtime_uid_gid_pair "$uid" "$gid" "$root/deploy/.env"
    return $?
  fi

  if [[ "$(id -u)" == '0' ]]; then
    status=0
    resolved="$(runtime_permissions_running_runtime_uid_gid)" || status=$?
    if [[ "$status" == "0" ]]; then
      printf '%s\n' "$resolved"
      return 0
    fi
    case "$status" in
      4) return 4 ;;
    esac
    runtime_permissions_warn "root 执行时无法确定 OPENCLAW_RUNTIME_UID/GID；请先生成 deploy/.env，或显式设置 OPENCLAW_RUNTIME_UID 与 OPENCLAW_RUNTIME_GID。"
    return 4
  fi

  printf '%s:%s\n' "$(id -u)" "$(id -g)"
}

runtime_permissions_assert_root_runtime_uid_gid_resolvable() {
  local root="$1"
  [[ "$(id -u)" == '0' ]] || return 0
  runtime_permissions_resolve_runtime_uid_gid "$root" >/dev/null
}

runtime_permissions_align_openclaw_owner_only() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  runtime_permissions_find_chmod_existing 700 "$dir" -type d
  runtime_permissions_find_chmod_existing 600 "$dir" -type f
  runtime_permissions_restore_extension_env_exec_bits "$dir"
}

runtime_permissions_align_repo_local_state_owner_only() {
  local root="$1"
  local dir="$2"
  local uid_gid=""
  local uid=""
  local gid=""
  local abs_root=""
  local abs_dir=""
  [[ -e "$dir" ]] || return 0
  [[ -d "$dir" ]] || { runtime_permissions_warn "repo-local state 路径不是目录：$dir"; return 4; }
  runtime_permissions_align_openclaw_owner_only "$dir"
  uid_gid="$(runtime_permissions_resolve_runtime_uid_gid "$root")"
  uid="${uid_gid%%:*}"
  gid="${uid_gid##*:}"
  [[ "$(id -u)" != "$uid" || "$(id -g)" != "$gid" ]] || return 0
  if [[ "$(id -u)" != '0' ]]; then
    runtime_permissions_warn "当前用户与 OPENCLAW_RUNTIME_UID/GID 不一致，且非 root；请先收口 repo-local state owner：$dir"
    return 0
  fi
  abs_root="$(runtime_permissions_abs_path "$root" "$root")"
  abs_dir="$(runtime_permissions_abs_path "$root" "$dir")"
  case "$abs_dir" in
    "$abs_root/state/image_pull"|"$abs_root/state/image_pull"/*|"$abs_root/state/image_artifacts"|"$abs_root/state/image_artifacts"/*) ;;
    *)
      runtime_permissions_warn "拒绝 chown 非 repo-local image state 目录：$abs_dir"
      return 4
      ;;
  esac
  chown -R "$uid:$gid" "$abs_dir"
  runtime_permissions_align_openclaw_owner_only "$abs_dir"
}

runtime_permissions_restore_extension_env_exec_bits() {
  local dir="$1"
  local extension_envs_dir="$dir/control_plane/extension_envs"
  [[ -d "$extension_envs_dir" ]] || return 0
  runtime_permissions_find_chmod_existing 700 "$extension_envs_dir" -type d
  runtime_permissions_find_chmod_existing 700 "$extension_envs_dir" -path '*/bin/*' -type f
}

runtime_permissions_align_openclaw_runtime_owner_only() {
  local root="$1"
  local dir="$2"
  local uid_gid=""
  local uid=""
  local gid=""
  local abs_dir=""
  local state_root=""
  runtime_permissions_align_openclaw_owner_only "$dir"
  uid_gid="$(runtime_permissions_resolve_runtime_uid_gid "$root")"
  uid="${uid_gid%%:*}"
  gid="${uid_gid##*:}"
  [[ "$(id -u)" != "$uid" || "$(id -g)" != "$gid" ]] || return 0
  if [[ "$(id -u)" != '0' ]]; then
    runtime_permissions_warn "当前用户与 OPENCLAW_RUNTIME_UID/GID 不一致，且非 root；请先收口 bind mount owner：$dir"
    return 0
  fi
  abs_dir="$(runtime_permissions_abs_path "$root" "$dir")"
  state_root="$(runtime_permissions_host_state_root "$root")"
  case "$abs_dir" in
    "$state_root"|"$state_root"/*) ;;
    *)
      runtime_permissions_warn "拒绝 chown 非 host state 目录：$abs_dir"
      return 4
      ;;
  esac
  chown -R "$uid:$gid" "$abs_dir"
  runtime_permissions_align_openclaw_owner_only "$abs_dir"
}

runtime_permissions_harden_bootstrap_outputs() {
  local root="$1"
  local state_dir=""
  local gateway_dir=""
  local control_plane_dir=""
  local setup_dir=""
  local host_setup_dir=""
  local dispatch_dir=""
  local local_ro_dir=""
  state_dir="$(runtime_permissions_host_state_root "$root")"
  gateway_dir="$state_dir/gateway"
  control_plane_dir="$state_dir/control_plane"
  host_setup_dir="$state_dir/setup"
  setup_dir="$control_plane_dir/setup"
  dispatch_dir="$control_plane_dir/dispatch"
  local_ro_dir="$gateway_dir/local_ro_gateway"
  runtime_permissions_chmod_if_exists 700 "$state_dir"
  runtime_permissions_chmod_if_exists 700 "$gateway_dir"
  runtime_permissions_chmod_if_exists 700 "$control_plane_dir"
  runtime_permissions_chmod_if_exists 700 "$host_setup_dir"
  runtime_permissions_chmod_if_exists 700 "$setup_dir"
  runtime_permissions_chmod_if_exists 700 "$dispatch_dir"
  runtime_permissions_chmod_if_exists 600 "$gateway_dir/openclaw.json"
  runtime_permissions_chmod_if_exists 600 "$dispatch_dir/targets.json"
  runtime_permissions_chmod_if_exists 600 "$host_setup_dir/dispatch_target_registry_summary.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/dispatch_target_registry_summary.json"
  runtime_permissions_chmod_if_exists 600 "$setup_dir/dispatch_runtime_summary.json"
  runtime_permissions_chmod_if_exists 600 "$control_plane_dir/runtime.scheduler.app.env"
  runtime_permissions_chmod_if_exists 600 "$control_plane_dir/runtime.internal-api.app.env"
  if [[ -d "$local_ro_dir" ]]; then
    runtime_permissions_find_chmod_existing 700 "$local_ro_dir" -type d
    runtime_permissions_find_chmod_existing 600 "$local_ro_dir" -type f
  fi
}

runtime_permissions_align_release_evidence_readable() {
  local root="$1"
  local rel=""
  local abs=""
  local control_plane_dir=""
  local evidence_dir=""
  control_plane_dir="$(runtime_permissions_host_control_plane_state_dir "$root")"
  evidence_dir="$control_plane_dir/release/evidence"
  [[ -d "$evidence_dir" ]] || return 0
  runtime_permissions_find_chmod_existing 700 "$evidence_dir" -type d
  runtime_permissions_find_chmod_existing 600 "$evidence_dir" -type f
  runtime_permissions_chmod_if_exists 700 "$control_plane_dir/release"
  runtime_permissions_chmod_if_exists 700 "$evidence_dir"
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    abs="$control_plane_dir/${rel#./}"
    if [[ -d "$abs" ]]; then
      runtime_permissions_chmod_if_exists 700 "$abs"
    else
      runtime_permissions_chmod_if_exists 600 "$abs"
    fi
  done < <(runtime_permissions_governed_release_paths "$root")
}

runtime_permissions_collect_repo_exec_candidates() {
  local root="$1"
  local path=''
  local -a explicit_targets=(
    "$root/scripts"
    "$root/deploy/nginx"
    "$root/agent/extensions"
    "$root/scripts/runtime/container_openclaw_cli"
    "$root/scripts/runtime/container_python"
  )

  for path in "${explicit_targets[@]}"; do
    case "$path" in
      "$root/scripts")
        [[ -d "$path" ]] || continue
        find "$path" -type f \( -name '*.sh' -o -path "$root/scripts/runtime/container_openclaw_cli" -o -path "$root/scripts/runtime/container_python" \) -print0 2>/dev/null
        ;;
      "$root/deploy/nginx")
        [[ -d "$path" ]] || continue
        find "$path" -maxdepth 1 -type f -name '*.sh' -print0 2>/dev/null
        ;;
      "$root/agent/extensions")
        [[ -d "$path" ]] || continue
        find "$path" \( -path '*/agent/modules/*/bin/*' -o -path '*/scripts/*.sh' \) -type f -print0 2>/dev/null
        ;;
      *)
        [[ -f "$path" ]] || continue
        printf '%s\0' "$path"
        ;;
    esac
  done | awk -v RS='\0' 'NF { if (!seen[$0]++) printf "%s%c", $0, 0 }'
}

runtime_permissions_mark_shebang_executable() {
  local root="$1"
  local file=""
  local -a batch=()
  while IFS= read -r -d '' file; do
    batch+=("$file")
    if ((${#batch[@]} >= 64)); then
      chmod 755 "${batch[@]}"
      batch=()
    fi
  done < <(runtime_permissions_collect_repo_exec_candidates "$root")
  if ((${#batch[@]} > 0)); then
    chmod 755 "${batch[@]}"
  fi
}

runtime_permissions_harden_certs() {
  local root="$1"
  if [[ -d "$root/deploy/nginx/certs" ]]; then
    runtime_permissions_align_repo_local_runtime_bind_owner "$root" "$root/deploy/nginx/certs"
    runtime_permissions_assert_access_mode "$root/deploy/nginx/certs" rwx "private ingress 证书目录" || return $?
    find "$root/deploy/nginx/certs" -type d -exec chmod 700 {} +
    find "$root/deploy/nginx/certs" -type f -exec chmod 600 {} +
  fi
}

runtime_permissions_require_setfacl() {
  command -v setfacl >/dev/null 2>&1 || {
    runtime_permissions_warn '缺少 setfacl；无法为 cap-drop 后的 private ingress 容器授予证书读取与 nginx 日志写入 ACL。请先执行 prepare_docker_host.sh --install-base-tools 或安装 acl 包。'
    return 4
  }
}

runtime_permissions_apply_acl() {
  local acl_spec="$1"
  local path="$2"
  [[ -e "$path" ]] || return 0
  runtime_permissions_require_setfacl || return $?
  setfacl -m "$acl_spec" "$path"
}

runtime_permissions_selinux_active() {
  command -v getenforce >/dev/null 2>&1 || return 1
  local mode=""
  mode="$(getenforce 2>/dev/null || true)"
  [[ "$mode" == "Enforcing" || "$mode" == "Permissive" ]]
}

runtime_permissions_apply_container_selinux_context() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  runtime_permissions_selinux_active || return 0
  command -v chcon >/dev/null 2>&1 || {
    runtime_permissions_warn "SELinux 已启用，但缺少 chcon；无法为容器 bind mount 标记可读上下文：$path"
    return 4
  }
  if [[ -d "$path" ]]; then
    chcon -Rt svirt_sandbox_file_t "$path" 2>/dev/null || {
      runtime_permissions_warn "无法为容器 bind mount 标记 SELinux 上下文：$path"
      return 4
    }
  else
    chcon -t svirt_sandbox_file_t "$path" 2>/dev/null || {
      runtime_permissions_warn "无法为容器 bind mount 标记 SELinux 上下文：$path"
      return 4
    }
  fi
}

runtime_permissions_prepare_runtime_bind_mount_selinux_contexts() {
  local root="$1"
  local state_root=""
  local path=""
  state_root="$(runtime_permissions_host_state_root "$root")"
  local -a bind_mount_paths=(
    "$state_root/gateway"
    "$state_root/control_plane"
    "$root/python"
    "$root/scripts"
    "$root/config"
    "$root/docs"
    "$root/agent"
  )
  for path in "${bind_mount_paths[@]}"; do
    runtime_permissions_apply_container_selinux_context "$path" || return $?
  done
}

runtime_permissions_prepare_ingress_nginx_conf_acl() {
  local root="$1"
  local nginx_conf=""
  nginx_conf="$(runtime_permissions_host_gateway_file "$root" nginx.gateway.conf)"
  [[ -e "$nginx_conf" ]] || return 0
  if [[ -d "$nginx_conf" ]]; then
    runtime_permissions_warn "private ingress Nginx 配置路径应为文件但当前是目录：$nginx_conf；请删除该目录并重新渲染 nginx.gateway.conf。"
    return 4
  fi
  chmod 600 "$nginx_conf"
  runtime_permissions_apply_acl u:0:r "$nginx_conf" || return $?
  runtime_permissions_apply_container_selinux_context "$nginx_conf" || return $?
}

runtime_permissions_prepare_ingress_log_acl() {
  local root="$1"
  local nginx_log_dir=""
  nginx_log_dir="$(runtime_permissions_host_gateway_file "$root" logs/nginx-gateway)"
  runtime_permissions_mkdir_chmod 700 "$nginx_log_dir"
  runtime_permissions_touch_chmod 600 "$nginx_log_dir/access.log"
  runtime_permissions_touch_chmod 600 "$nginx_log_dir/error.log"
  runtime_permissions_align_openclaw_runtime_owner_only "$root" "$nginx_log_dir" || return $?
  runtime_permissions_apply_acl u:0:rwx "$nginx_log_dir" || return $?
  runtime_permissions_apply_acl d:u:0:rw "$nginx_log_dir" || return $?
  runtime_permissions_apply_acl u:0:rw "$nginx_log_dir/access.log" || return $?
  runtime_permissions_apply_acl u:0:rw "$nginx_log_dir/error.log" || return $?
  runtime_permissions_apply_container_selinux_context "$nginx_log_dir" || return $?
}

runtime_permissions_prepare_ingress_cert_acl() {
  local root="$1"
  local cert_dir="$root/deploy/nginx/certs"
  [[ -d "$cert_dir" ]] || return 0
  find "$cert_dir" -type d -exec chmod 700 {} +
  find "$cert_dir" -type f -exec chmod 600 {} +
  while IFS= read -r -d '' dir; do
    runtime_permissions_apply_acl u:0:rx "$dir" || return $?
  done < <(find "$cert_dir" -type d -print0)
  while IFS= read -r -d '' file; do
    runtime_permissions_apply_acl u:0:r "$file" || return $?
  done < <(find "$cert_dir" -type f \( -name '*.crt' -o -name '*.key' -o -name '*.pem' \) -print0)
  runtime_permissions_apply_container_selinux_context "$cert_dir" || return $?
}

runtime_permissions_prepare_ingress_cap_drop_mount_access() {
  local root="$1"
  runtime_permissions_prepare_ingress_nginx_conf_acl "$root"
  runtime_permissions_prepare_ingress_log_acl "$root"
  runtime_permissions_prepare_ingress_cert_acl "$root"
}
