#!/usr/bin/env bash
# 用途：统一维护宿主机 Python 静态禁令扫描逻辑，避免多个门禁脚本各自复制规则。

PYTHON_RUNTIME_GUARD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$PYTHON_RUNTIME_GUARD_LIB_DIR/repo_root.sh"
PYTHON_RUNTIME_GUARD_ROOT_DIR="$(openclaw_repo_root_from "$PYTHON_RUNTIME_GUARD_LIB_DIR")"
# shellcheck source=repo_contracts.sh
source "$PYTHON_RUNTIME_GUARD_ROOT_DIR/scripts/lib/repo_contracts.sh"
PYTHON_RUNTIME_GUARD_SHELL_SCANNER="${PYTHON_RUNTIME_GUARD_SHELL_SCANNER:-$PYTHON_RUNTIME_GUARD_LIB_DIR/host_python_shell_guard.sh}"
PYTHON_RUNTIME_GUARD_SURFACE_JSON="${PYTHON_RUNTIME_GUARD_SURFACE_JSON:-}"
repo_contract_default_path PYTHON_RUNTIME_GUARD_SURFACE_JSON governance.host_python_governance

python_runtime_guard_have_rg() {
  command -v rg >/dev/null 2>&1
}

python_runtime_guard_can_run_container_scanners() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 || return 1
  local image_env_script="$PYTHON_RUNTIME_GUARD_LIB_DIR/image_env.sh"
  [[ -f "$image_env_script" && -r "$image_env_script" ]] || return 1
  local py_image=''
  py_image="$(ROOT_DIR="$PYTHON_RUNTIME_GUARD_ROOT_DIR" bash -c 'source "$1"; image_env_load >/dev/null 2>&1; printf "%s" "$OPENCLAW_CONTROL_PLANE_IMAGE"' _ "$image_env_script" 2>/dev/null || true)"
  [[ -n "$py_image" ]] || return 1
  docker image inspect "$py_image" >/dev/null 2>&1 || return 1
}

python_runtime_guard_search_extended_in_paths() {
  local pattern="$1"
  shift
  local path=''
  local results=''
  local chunk=''
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    if [[ -d "$path" ]]; then
      chunk="$(LC_ALL=C grep -R -H -n -I -E -- "$pattern" "$path" 2>/dev/null || true)"
    else
      chunk="$(LC_ALL=C grep -H -n -I -E -- "$pattern" "$path" 2>/dev/null || true)"
    fi
    [[ -z "$chunk" ]] || results+="$chunk"$'
'
  done
  [[ -z "$results" ]] || printf '%s' "${results%$'
'}"
}

python_runtime_guard_search_fixed_in_paths() {
  local needle="$1"
  shift
  local path=''
  local results=''
  local chunk=''
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    if [[ -d "$path" ]]; then
      chunk="$(LC_ALL=C grep -R -H -n -I -F -- "$needle" "$path" 2>/dev/null || true)"
    else
      chunk="$(LC_ALL=C grep -H -n -I -F -- "$needle" "$path" 2>/dev/null || true)"
    fi
    [[ -z "$chunk" ]] || results+="$chunk"$'
'
  done
  [[ -z "$results" ]] || printf '%s' "${results%$'
'}"
}

python_runtime_guard_capture_shell_host_python_scan() {
  local root_dir="$1"
  local __out_var="$2"
  local __status_var="$3"
  shift 3
  [[ -f "$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" && -r "$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" ]] || { echo "[python_runtime_guard][FAIL] shell 扫描器不可读：$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" >&2; return 97; }
  local output
  local status
  set +e
  output="$(bash "$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" --repo-root "$root_dir" "$@" 2>&1)"
  status=$?
  set -e
  printf -v "$__out_var" '%s' "$output"
  printf -v "$__status_var" '%s' "$status"
}

python_runtime_guard_require_surface_json() {
  [[ -f "$PYTHON_RUNTIME_GUARD_SURFACE_JSON" && -r "$PYTHON_RUNTIME_GUARD_SURFACE_JSON" ]] || {
    echo "[python_runtime_guard][FAIL] runner surface 真源不可读：$PYTHON_RUNTIME_GUARD_SURFACE_JSON" >&2
    return 97
  }
  command -v jq >/dev/null 2>&1 || {
    echo '[python_runtime_guard][FAIL] 缺少 jq；无法读取 host python governance 真源。' >&2
    return 97
  }
}

python_runtime_guard_jq_lines() {
  local filter="$1"
  python_runtime_guard_require_surface_json || return $?
  jq -r "$filter" "$PYTHON_RUNTIME_GUARD_SURFACE_JSON" | tr -d '\r'
}

python_runtime_guard_iter_shell_scan_manifest() {
  python_runtime_guard_jq_lines '.shell_scan_manifest | to_entries[] as $row | $row.value[] | "\($row.key) \(.)"'
}

python_runtime_guard_iter_manifest_paths() {
  local wanted_kind="$1"
  python_runtime_guard_require_surface_json || return $?
  jq -r --arg kind "$wanted_kind" '.shell_scan_manifest[$kind][]' "$PYTHON_RUNTIME_GUARD_SURFACE_JSON"
}

python_runtime_guard_collect_broad_shell_targets() {
  local root_dir="$1"

  local -A skip_paths=()
  local rel_path
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    skip_paths["$rel_path"]=1
  done < <(
    python_runtime_guard_iter_manifest_paths self
    python_runtime_guard_iter_manifest_paths skip
  )

  while IFS= read -r -d '' rel_path; do
    [[ -n "$rel_path" ]] || continue
    rel_path="${rel_path#./}"
    [[ -n "$rel_path" ]] || continue
    [[ -n "${skip_paths[$rel_path]:-}" ]] && continue
    printf '%s\0' "$root_dir/$rel_path"
  done < <(
    cd "$root_dir"
    find . \
      -path './.git' -prune -o \
      -type f -name '*.sh' -print0 | sort -z
  )
}

python_runtime_guard_iter_deleted_stub_ref_scan_roots() {
  python_runtime_guard_jq_lines '.deleted_stub_ref_scan_roots[]'
}

python_runtime_guard_iter_agent_launcher_manifest() {
  python_runtime_guard_jq_lines '.agent_launcher_manifest[] | "\(.agentRef) \(.relPath)"'
}

python_runtime_guard_iter_agent_readme_manifest() {
  python_runtime_guard_jq_lines '.agent_readme_manifest[]'
}

python_runtime_guard_verify_agent_launcher_contract() {
  local root_dir="$1"
  local agent_ref=""
  local rel_path=""
  local target_file=""
  local first_line=""
  local runtime_entrypoint="$root_dir/scripts/agent_runtime/run_agent_entrypoint.sh"
  local dollar='$'
  while read -r agent_ref rel_path; do
    [[ -n "$agent_ref" && -n "$rel_path" ]] || continue
    target_file="$root_dir/$rel_path"
    [[ -f "$target_file" ]] || { echo "缺少 agent 薄启动器：$rel_path"; return 1; }
    first_line="$(sed -n '1p' "$target_file")"
    [[ "$first_line" == '#!/usr/bin/env bash' ]] || { echo "$rel_path 不是 bash 薄启动器"; return 1; }
    grep -Fq 'scripts/agent_runtime/run_agent_entrypoint.sh' "$target_file" || {
      echo "$rel_path 未固定收口到 scripts/agent_runtime/run_agent_entrypoint.sh"
      return 1
    }
  done < <(python_runtime_guard_iter_agent_launcher_manifest)

  [[ -f "$runtime_entrypoint" ]] || { echo '缺少 scripts/agent_runtime/run_agent_entrypoint.sh'; return 1; }
  grep -Fq 'scripts/runtime/run_openclaw_python_tool.sh" control-plane runtime scheduler-run-agent-runtime --config-path "$RESOLVED_CONFIG_PATH" --agent-ref "$AGENT_REF" -- "$@"' "$runtime_entrypoint" || {
    echo 'scripts/agent_runtime/run_agent_entrypoint.sh 未固定 control-plane agent 统一入口'
    return 1
  }
  if grep -Fq 'scripts/control_plane/run_registered_agent_runtime.sh' "$runtime_entrypoint"; then
    echo 'scripts/agent_runtime/run_agent_entrypoint.sh 依赖非正式 control_plane 转发入口'
    return 1
  fi
  grep -Fq -- "--agent-ref \"${dollar}AGENT_REF\"" "$runtime_entrypoint" || {
    echo 'scripts/agent_runtime/run_agent_entrypoint.sh 未透传 --agent-ref "$AGENT_REF"'
    return 1
  }
  grep -Fq -- "--config-path \"${dollar}RESOLVED_CONFIG_PATH\"" "$runtime_entrypoint" || {
    echo 'scripts/agent_runtime/run_agent_entrypoint.sh 未透传 --config-path "$RESOLVED_CONFIG_PATH"'
    return 1
  }
  grep -Fq -- "-- \"${dollar}@\"" "$runtime_entrypoint" || {
    echo 'scripts/agent_runtime/run_agent_entrypoint.sh 未透传 -- "$@"'
    return 1
  }
}

python_runtime_guard_verify_agent_readme_contract() {
  local root_dir="$1"
  local rel_path=""
  local target_file=""
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    target_file="$root_dir/$rel_path"
    [[ -f "$target_file" ]] || { echo "缺少 agent 文档：$rel_path"; return 1; }
    if LC_ALL=C grep -n -E 'PYTHONPATH=python[[:space:]]+python3?[[:space:]]+-m[[:space:]]+openclaw\.' "$target_file" >/dev/null 2>&1; then
      echo "$rel_path 仍暴露宿主机 PYTHONPATH=python python -m openclaw 入口"
      return 1
    fi
    if LC_ALL=C grep -n -E 'python3?[[:space:]]+-m[[:space:]]+openclaw\.cli[[:space:]]+control-plane[[:space:]]+runtime[[:space:]]+run-agent-runtime' "$target_file" >/dev/null 2>&1; then
      echo "$rel_path 仍暴露未标注容器边界的 python -m openclaw.cli 入口"
      return 1
    fi
  done < <(python_runtime_guard_iter_agent_readme_manifest)
}

python_runtime_guard_collect_self_shell_targets() {
  local root_dir="$1"
  local rel_path
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    [[ -f "$root_dir/$rel_path" ]] || continue
    printf '%s\0' "$root_dir/$rel_path"
  done < <(python_runtime_guard_iter_manifest_paths self)
}

python_runtime_guard_run_shell_scan_for_targets() {
  local root_dir="$1"
  local __out_var="$2"
  local __status_var="$3"
  shift 3
  local -a targets=("$@")
  if (( ${#targets[@]} == 0 )); then
    printf -v "$__out_var" ''
    printf -v "$__status_var" '0'
    return 0
  fi
  python_runtime_guard_capture_shell_host_python_scan "$root_dir" "$__out_var" "$__status_var" "${targets[@]}"
}

python_runtime_guard_find_host_python_refs_in_file() {
  local target_file="$1"
  local repo_root="${2:-$(dirname "$target_file")}"
  [[ -f "$target_file" ]] || return 0
  while [[ "$repo_root" != "/" && ! -d "$repo_root/scripts" ]]; do
    repo_root="$(dirname "$repo_root")"
  done
  [[ -d "$repo_root/scripts" ]] || repo_root="$(dirname "$target_file")"
  local scan_output=""
  local scan_status=0
  python_runtime_guard_capture_shell_host_python_scan "$repo_root" scan_output scan_status "$target_file"
  case "$scan_status" in
    0) return 0 ;;
    1) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output"; return 0 ;;
    *) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output" >&2; return "$scan_status" ;;
  esac
}

python_runtime_guard_find_host_python_shell_refs() {
  local root_dir="$1"
  local -a targets=()
  local abs_path
  while IFS= read -r -d '' abs_path; do
    [[ -n "$abs_path" ]] || continue
    targets+=("$abs_path")
  done < <(python_runtime_guard_collect_broad_shell_targets "$root_dir")
  local scan_output=""
  local scan_status=0
  python_runtime_guard_run_shell_scan_for_targets "$root_dir" scan_output scan_status "${targets[@]}"
  case "$scan_status" in
    0) return 0 ;;
    1) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output"; return 1 ;;
    *) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output" >&2; return "$scan_status" ;;
  esac
}

python_runtime_guard_iter_self_scan_targets() {
  python_runtime_guard_iter_manifest_paths self
}

python_runtime_guard_iter_runner_binding_manifest() {
  python_runtime_guard_jq_lines '.runner_binding_manifest[] | "\(.rootVar) \(.relPath)"'
}

python_runtime_guard_verify_runner_binding_contract() {
  local root_dir="$1"
  local root_var=""
  local rel_path=""
  local target_file=""
  local pattern=""
  while read -r root_var rel_path; do
    [[ -n "$root_var" && -n "$rel_path" ]] || continue
    target_file="$root_dir/$rel_path"
    [[ -f "$target_file" ]] || { echo "缺少关键脚本：$rel_path"; return 1; }
    printf -v pattern '^PYTHON_RUNNER="\$%s/scripts/runtime/run_python_container\.sh"$' "$root_var"
    LC_ALL=C grep -n -E -- "$pattern" "$target_file" >/dev/null 2>&1 || {
      echo "$rel_path 未固定绑定容器入口"
      return 1
    }
  done < <(python_runtime_guard_iter_runner_binding_manifest)
}

python_runtime_guard_iter_runner_surface_literal_manifest() {
  python_runtime_guard_jq_lines '.runner_surface_literal_manifest[] | "\(.relPath)\t\(.literal)"'
}
python_runtime_guard_iter_runner_surface_allowlist() {
  python_runtime_guard_jq_lines '.runner_surface_allowlist[]'
}

python_runtime_guard_verify_runner_surface_contract() {
  local root_dir="$1"
  local -A covered_paths=()
  local root_var=""
  local rel_path=""
  local target_file=""
  local literal=""

  while read -r root_var rel_path; do
    [[ -n "$root_var" && -n "$rel_path" ]] || continue
    covered_paths["$rel_path"]=1
  done < <(python_runtime_guard_iter_runner_binding_manifest)

  while IFS=$'\t' read -r rel_path literal; do
    [[ -n "$rel_path" && -n "$literal" ]] || continue
    target_file="$root_dir/$rel_path"
    [[ -f "$target_file" ]] || { echo "缺少关键脚本：$rel_path"; return 1; }
    grep -Fq "$literal" "$target_file" || {
      echo "$rel_path 未固定声明容器 Python runner"
      return 1
    }
    covered_paths["$rel_path"]=1
  done < <(python_runtime_guard_iter_runner_surface_literal_manifest)

  while IFS= read -r rel_path; do
    rel_path="${rel_path# }"
    [[ -n "$rel_path" ]] || continue
    covered_paths["$rel_path"]=1
  done < <(python_runtime_guard_iter_runner_surface_allowlist)

  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    [[ -n "${covered_paths[$rel_path]:-}" ]] || {
      echo "$rel_path 引用了 run_python_container.sh，但未进入 runner surface manifest"
      return 1
    }
  done < <(
    cd "$root_dir"
    find scripts -type f -name '*.sh' -print0 2>/dev/null \
      | while IFS= read -r -d '' rel_file; do
          LC_ALL=C grep -Fq 'run_python_container.sh' "$rel_file" 2>/dev/null || continue
          printf '%s
' "$rel_file"
        done | sort
  )
}

python_runtime_guard_find_self_host_python_refs() {
  local root_dir="$1"
  local -a targets=()
  local abs_path
  while IFS= read -r -d '' abs_path; do
    [[ -n "$abs_path" ]] || continue
    targets+=("$abs_path")
  done < <(python_runtime_guard_collect_self_shell_targets "$root_dir")
  local scan_output=""
  local scan_status=0
  python_runtime_guard_run_shell_scan_for_targets "$root_dir" scan_output scan_status "${targets[@]}"
  case "$scan_status" in
    0) return 0 ;;
    1) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output"; return 0 ;;
    *) [[ -z "$scan_output" ]] || printf '%s\n' "$scan_output" >&2; return "$scan_status" ;;
  esac
}

python_runtime_guard_verify_runtime_image_contract() {
  local root_dir="$1"
  # shellcheck source=repo_contracts.sh
  source "$PYTHON_RUNTIME_GUARD_LIB_DIR/repo_contracts.sh"
  local runtime_image_pin_rel_path=''
  repo_contract_assign_relpath runtime_image_pin_rel_path image_pins.runtime || return 1
  local runtime_image_pin_file="$root_dir/$runtime_image_pin_rel_path"
  local python_compose_image_ref="\${OPENCLAW_RUNTIME_PYTHON_IMAGE:?OPENCLAW_RUNTIME_PYTHON_IMAGE_required}"
  local nginx_compose_image_ref="\${NGINX_IMAGE:?NGINX_IMAGE_required}"
  local python_pin
  local python_tag
  local python_digest
  local nginx_pin
  local nginx_tag
  local nginx_digest

  [[ -f "$runtime_image_pin_file" ]] || { echo "缺少部署镜像 pin 真源：$runtime_image_pin_rel_path"; return 1; }
  python_pin="$(grep '^OPENCLAW_RUNTIME_PYTHON_IMAGE=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  python_tag="$(grep '^OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  python_digest="$(grep '^OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  nginx_pin="$(grep '^NGINX_IMAGE=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  nginx_tag="$(grep '^NGINX_IMAGE_TAG=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  nginx_digest="$(grep '^NGINX_IMAGE_DIGEST=' "$runtime_image_pin_file" | head -n1 | cut -d= -f2-)"
  [[ -n "$python_pin" ]] || { echo "部署镜像 pin 真源缺少 OPENCLAW_RUNTIME_PYTHON_IMAGE"; return 1; }
  [[ -n "$python_tag" ]] || { echo "部署镜像 pin 真源缺少 OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG"; return 1; }
  [[ -n "$python_digest" ]] || { echo "部署镜像 pin 真源缺少 OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST"; return 1; }
  [[ -n "$nginx_pin" ]] || { echo "部署镜像 pin 真源缺少 NGINX_IMAGE"; return 1; }
  [[ -n "$nginx_tag" ]] || { echo "部署镜像 pin 真源缺少 NGINX_IMAGE_TAG"; return 1; }
  [[ -n "$nginx_digest" ]] || { echo "部署镜像 pin 真源缺少 NGINX_IMAGE_DIGEST"; return 1; }
  [[ "$python_pin" == *"@${python_digest}" ]] || { echo "OPENCLAW_RUNTIME_PYTHON_IMAGE 与 OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST 不一致"; return 1; }
  [[ "$python_pin" == *":${python_tag}@${python_digest}" ]] || { echo "OPENCLAW_RUNTIME_PYTHON_IMAGE 与 OPENCLAW_RUNTIME_PYTHON_IMAGE_TAG / OPENCLAW_RUNTIME_PYTHON_IMAGE_DIGEST 不一致"; return 1; }
  [[ "$nginx_pin" == *"@${nginx_digest}" ]] || { echo "NGINX_IMAGE 与 NGINX_IMAGE_DIGEST 不一致"; return 1; }
  [[ "$nginx_pin" == *":${nginx_tag}@${nginx_digest}" ]] || { echo "NGINX_IMAGE 与 NGINX_IMAGE_TAG / NGINX_IMAGE_DIGEST 不一致"; return 1; }
  [[ "$python_pin" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "OPENCLAW_RUNTIME_PYTHON_IMAGE 未收紧到 tag@digest pin"; return 1; }
  [[ "$nginx_pin" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "NGINX_IMAGE 未收紧到 tag@digest pin"; return 1; }
  grep -Fq "OPENCLAW_RUNTIME_PYTHON_IMAGE=$python_pin" "$runtime_image_pin_file" || { echo "runtime 真源未锁定默认 Python 运行时镜像"; return 1; }
  grep -Fq "NGINX_IMAGE=$nginx_pin" "$runtime_image_pin_file" || { echo "runtime 真源未锁定默认 Nginx 运行时镜像"; return 1; }
  grep -Fq "$python_compose_image_ref" "$root_dir/deploy/docker-compose.yml" || { echo "docker-compose.yml 未通过严格变量引用 OPENCLAW_RUNTIME_PYTHON_IMAGE"; return 1; }
  grep -Fq "$nginx_compose_image_ref" "$root_dir/deploy/docker-compose.yml" || { echo "docker-compose.yml 未通过严格变量引用 NGINX_IMAGE"; return 1; }
}

python_runtime_guard_iter_doc_scan_roots() {
  python_runtime_guard_jq_lines '.doc_scan_roots[]'
}

python_runtime_guard_find_doc_host_python_refs() {
  local root_dir="$1"
  local -a scan_roots=()
  local rel_path
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    [[ -e "$root_dir/$rel_path" ]] || continue
    scan_roots+=("$root_dir/$rel_path")
  done < <(python_runtime_guard_iter_doc_scan_roots)
  (( ${#scan_roots[@]} > 0 )) || return 0
  bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" guards host-python-doc --repo-root "$root_dir" "${scan_roots[@]}"
}

python_runtime_guard_capture_doc_host_python_scan() {
  local root_dir="$1"
  local __out_var="$2"
  local __status_var="$3"
  local output
  local status
  set +e
  output="$(python_runtime_guard_find_doc_host_python_refs "$root_dir" 2>&1)"
  status=$?
  set -e
  printf -v "$__out_var" '%s' "$output"
  printf -v "$__status_var" '%s' "$status"
}

python_runtime_guard_doc_line_has_uncovered_match() {
  local pattern="$1"
  local text="${2:-}"
  LINE_TEXT="$text" SCAN_PATTERN="$pattern" LC_ALL=C awk '
    function append_ranges(text, pattern,    cursor, segment, start, end) {
      cursor = 1
      while (cursor <= length(text)) {
        segment = substr(text, cursor)
        if (!match(segment, pattern)) {
          break
        }
        start = cursor + RSTART - 1
        end = start + RLENGTH - 1
        allow_count += 1
        allow_start[allow_count] = start
        allow_end[allow_count] = end
        cursor = end + 1
      }
    }

    function span_is_covered(start, end,    i) {
      for (i = 1; i <= allow_count; i += 1) {
        if (allow_start[i] <= start && end <= allow_end[i]) {
          return 1
        }
      }
      return 0
    }

    BEGIN {
      text = ENVIRON["LINE_TEXT"]
      pattern = ENVIRON["SCAN_PATTERN"]
      allow_count = 0
      append_ranges(text, "(^|[^[:alnum:]_./-])python3?[[:space:]]+-m[[:space:]]+openclaw\\.testing\\.repo_host($|[^[:alnum:]_./-])")
      append_ranges(text, "(^|[^[:alnum:]_./-])python3?[[:space:]]+-m[[:space:]]+unittest[[:space:]]+openclaw([[:alnum:]_.-]*)($|[^[:alnum:]_./-])")

      cursor = 1
      while (cursor <= length(text)) {
        segment = substr(text, cursor)
        if (!match(segment, pattern)) {
          exit 1
        }
        start = cursor + RSTART - 1
        end = start + RLENGTH - 1
        if (!span_is_covered(start, end)) {
          exit 0
        }
        cursor = end + 1
      }
      exit 1
    }
  '
}

python_runtime_guard_has_docker_cli() {
  command -v docker >/dev/null 2>&1
}

python_runtime_guard_fallback_shell_scan_for_targets() {
  local root_dir="$1"
  shift
  local -a targets=("$@")
  local direct_pattern
  local wrapped_pattern
  local target=''
  local results=''
  local chunk=''
  (( ${#targets[@]} > 0 )) || return 0
  direct_pattern='(^|[;&|`{}()]|&&|\|\||\$\(|![[:space:]]*|((if|then|do|elif|while|until|case)[[:space:]]+))[[:space:]]*((([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+)|(env([[:space:]]+(-[^[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+))+)|(nohup)|(time([[:space:]]+-[^[:space:]]+)*)|((builtin[[:space:]]+)?command([[:space:]]+--)?)|(exec))[[:space:]]+)*(python3?|/usr/bin/python3?|/usr/bin/env[[:space:]]+python3?)($|[[:space:]])'
  wrapped_pattern='(^|[;&|`{}()]|&&|\|\||\$\(|![[:space:]]*|((if|then|do|elif|while|until|case)[[:space:]]+))[[:space:]]*((([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+)|(env([[:space:]]+(-[^[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+))+)|(nohup)|(time([[:space:]]+-[^[:space:]]+)*)|((builtin[[:space:]]+)?command([[:space:]]+--)?)|(exec))[[:space:]]+)*(sh|bash)([[:space:]]+-[^[:space:]]+)*[[:space:]]+-(l?c)[[:space:]]+["'"'"']?.*(python3?|/usr/bin/python3?|/usr/bin/env[[:space:]]+python3?)'
  for target in "${targets[@]}"; do
    [[ -f "$target" ]] || continue
    chunk="$(LC_ALL=C grep -H -n -E -e "$direct_pattern" -e "$wrapped_pattern" "$target" 2>/dev/null || true)"
    [[ -z "$chunk" ]] || results+="$chunk"$'
'
  done
  [[ -z "$results" ]] || {
    printf '%s
' "${results%$'
'}"
    return 1
  }
  return 0
}

python_runtime_guard_fallback_doc_scan() {
  local root_dir="$1"
  local -a scan_roots=()
  local rel_path
  local pattern
  local raw_results=""
  local results=""
  local raw_line=""
  local text=""
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    [[ -e "$root_dir/$rel_path" ]] || continue
    scan_roots+=("$root_dir/$rel_path")
  done < <(python_runtime_guard_iter_doc_scan_roots)
  (( ${#scan_roots[@]} > 0 )) || return 0
  pattern='(^|[[:space:]>`-])(PYTHONPATH=python[[:space:]]+python3?[[:space:]]+-m[[:space:]]+openclaw\.[^[:space:]]+|python3?[[:space:]]+-m[[:space:]]+openclaw\.[^[:space:]]+|python3?[[:space:]]+-m[[:space:]]+openclaw_ext_[^[:space:]]+)'
  raw_results="$(python_runtime_guard_search_extended_in_paths "$pattern" "${scan_roots[@]}")"
  while IFS= read -r raw_line; do
    [[ -n "$raw_line" ]] || continue
    text="${raw_line#*:*:}"
    if ! python_runtime_guard_doc_line_has_uncovered_match "$pattern" "$text"; then
      continue
    fi
    results+="$raw_line"$'\n'
  done <<< "$raw_results"
  results="${results%$'\n'}"
  [[ -z "$results" ]] || {
    printf '%s\n' "$results"
    return 1
  }
  return 0
}

python_runtime_guard_run_static_host_python_checks() {
  local root_dir="$1"
  local host_py_ref=""
  local self_refs=""
  local doc_ref=""
  local doc_status=0
  local shell_scan_status=0
  local self_scan_status=0
  local -a violations=()
  local -a broad_targets=()
  local -a self_targets=()
  local abs_path

  while IFS= read -r -d '' abs_path; do
    [[ -n "$abs_path" ]] || continue
    broad_targets+=("$abs_path")
  done < <(python_runtime_guard_collect_broad_shell_targets "$root_dir")

  while IFS= read -r -d '' abs_path; do
    [[ -n "$abs_path" ]] || continue
    self_targets+=("$abs_path")
  done < <(python_runtime_guard_collect_self_shell_targets "$root_dir")

  local can_use_container_shell=0
  local can_use_container_doc=0

  if python_runtime_guard_can_run_container_scanners; then
    [[ -f "$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" && -r "$PYTHON_RUNTIME_GUARD_SHELL_SCANNER" ]] && can_use_container_shell=1
    [[ -f "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" && -r "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" ]] && can_use_container_doc=1
  fi

  set +e
  if (( can_use_container_shell )); then
    host_py_ref="$(python_runtime_guard_find_host_python_shell_refs "$root_dir")"
    shell_scan_status=$?
    self_refs="$(python_runtime_guard_find_self_host_python_refs "$root_dir")"
    self_scan_status=$?
  else
    host_py_ref="$(python_runtime_guard_fallback_shell_scan_for_targets "$root_dir" "${broad_targets[@]}")"
    shell_scan_status=$?
    self_refs="$(python_runtime_guard_fallback_shell_scan_for_targets "$root_dir" "${self_targets[@]}")"
    self_scan_status=$?
  fi

  if (( can_use_container_doc )); then
    python_runtime_guard_capture_doc_host_python_scan "$root_dir" doc_ref doc_status
  else
    doc_ref="$(python_runtime_guard_fallback_doc_scan "$root_dir")"
    doc_status=$?
  fi
  set -e

  if [[ "$shell_scan_status" == "1" ]]; then
    [[ -z "$host_py_ref" ]] || violations+=("$host_py_ref")
  elif [[ "$shell_scan_status" != "0" ]]; then
    [[ -z "$host_py_ref" ]] || printf '%s\n' "$host_py_ref"
    return "$shell_scan_status"
  fi

  if [[ "$self_scan_status" == "1" ]]; then
    [[ -z "$self_refs" ]] || violations+=("$self_refs")
  elif [[ "$self_scan_status" != "0" ]]; then
    [[ -z "$self_refs" ]] || printf '%s\n' "$self_refs"
    return "$self_scan_status"
  fi

  if [[ "$doc_status" == "1" ]]; then
    [[ -z "$doc_ref" ]] || violations+=("$doc_ref")
  elif [[ "$doc_status" != "0" ]]; then
    [[ -z "$doc_ref" ]] || printf '%s\n' "$doc_ref"
    return "$doc_status"
  fi

  if (( ${#violations[@]} > 0 )); then
    printf '%s\n' "${violations[@]}"
    return 1
  fi
  return 0
}
