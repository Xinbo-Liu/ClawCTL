#!/usr/bin/env bash
# 用途：按 deploy/site.env + 扩展 extension.env + deploy/targets.d 真源生成运行态 deploy/.env，并输出仍待人工补齐的配置项。
set -Eeuo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
OPENCLAW_PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=scripts/lib/flow_summary_common_shell.sh
source "$ROOT_DIR/scripts/lib/flow_summary_common_shell.sh"
# shellcheck source=scripts/lib/flow_entry_surface_shell.sh
source "$ROOT_DIR/scripts/lib/flow_entry_surface_shell.sh"
# shellcheck source=scripts/setup/lib/setup_cli_common.sh
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
OUTPUT_PATH="$ROOT_DIR/deploy/.env"
SUMMARY_JSON_PATH="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/config_summary.json)"
SITE_ENV_PATH="$ROOT_DIR/deploy/site.env"
TARGETS_ENV_DIR="$ROOT_DIR/deploy/targets.d"
DRY_RUN=0
HELP_ONLY=0
EXPLAIN_ONLY=0
CONFIG_GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CONFIG_CURRENT_STAGE='runtime_user_preflight'
CONFIG_SUMMARY_EMITTED=0
CONFIG_FAILURE_MESSAGE=''

show_one_click_config_help() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/one_click_config.sh [选项]

默认行为：
  1) 从 deploy/site.env、启用扩展的 agent/extensions/<extension-id>/deploy/extension.env 与 deploy/targets.d 生成 deploy/.env
  2) 自动保留已有运行态随机 token；首次生成时自动写入 gateway token，并按启用扩展补齐扩展侧 token
  3) 自动补齐缺失的 deploy/site.env 与 active profile 的 deploy/targets.d/*.env.example 模板
  4) 首次生成 deploy/site.env 时，OPENCLAW_INGRESS_LISTEN_IP 默认优先写入 hostname -I 探测到的首个私网 IPv4
  5) 自动把 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID 初始写为当前执行用户的 UID/GID
  6) 将必须人工填写的项写为 __REQUIRED__
  7) 在终端打印仍待人工填写项
  8) 输出 <current-host-state-root>/control_plane/setup/config_summary.json 供后续脚本读取
  9) 执行前必须已显式完成 host 控制面执行介质准备

正式部署路径：
  - 官方 Gateway token auth
  - 单一私有 HTTPS ingress
  - Python 业务面仅在内网运行

用户边界：
  - 正式部署必须以固定部署用户执行；root 仅用于宿主机准备、ingress 边界规则物化和权限修复。
  - 若专用验证环境确需 root runtime 用户，显式设置 OPENCLAW_ALLOW_ROOT_RUNTIME_USER=1 后再执行。
USAGE
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

show_one_click_config_explain() {
  cat <<'EXPLAIN'
one_click_config 当前只负责生成部署真源，不触发 bootstrap / pull / build / compose；执行前必须已显式准备 host 控制面执行介质。

执行内容：
  1. 读取 deploy/site.env、启用扩展的 agent/extensions/<extension-id>/deploy/extension.env 与 deploy/targets.d/*.env
  2. 缺失时自动补齐 deploy/site.env 与 active profile 的 deploy/targets.d/*.env.example 模板
  3. 初次生成时自动写入 gateway token，并按启用扩展补齐扩展侧 token；已存在随机 token 会被保留
  4. deploy/site.env 首次生成时，OPENCLAW_INGRESS_LISTEN_IP 默认优先取 hostname -I 探测到的首个私网 IPv4
  5. 将 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID 默认写为当前执行用户
  6. 自动写入 image pins 真源与当前镜像真源默认值
  7. 预创建 config_summary 所需的最小 control_plane/setup 目录
  8. 生成 deploy/.env 与 <current-host-state-root>/control_plane/setup/config_summary.json
  9. 列出仍需人工填写的 __REQUIRED__ 项
 10. 控制面 render 失败时直接返回错误

执行前置条件：
  - 必须先执行 bash ./scripts/setup/prepare_control_plane_medium.sh；
  - 离线场景使用 bash ./scripts/setup/prepare_control_plane_medium.sh --offline --image-archive <local-path>。
EXPLAIN
  cat <<'EOF2'
补充说明：
  - 真正执行 render 时，host 控制面固定通过容器化 Python 进入 setup env render；
  - host 控制面执行介质统一由 prepare_control_plane_medium.sh 准备，one_click_config 只消费已就绪介质；
  - root 执行会在任何 deploy/.env、运行态目录或摘要文件写入前被拒绝，避免把 OPENCLAW_RUNTIME_UID/GID 写成 0:0；专用验证环境可显式设置 OPENCLAW_ALLOW_ROOT_RUNTIME_USER=1。
EOF2
  setup_help_surface_guarantee_text
  setup_help_surface_reference_text
}

config_fail() {
  CONFIG_FAILURE_MESSAGE="$1"
  echo "[one_click_config][FAIL] $1" >&2
  return 2
}

config_run_and_capture() {
  local __outvar="$1"
  shift
  local out='' rc=0
  set +e
  out="$("$@" 2>&1)"
  rc=$?
  set -e
  printf -v "$__outvar" '%s' "$out"
  return "$rc"
}

config_is_control_plane_preflight_output() {
  local output="$1"
  printf '%s' "$output" | grep -qiE '\[python_container\]|OPENCLAW_CONTROL_PLANE_IMAGE|docker daemon|docker\.sock|未检测到 docker|Python 工具必须通过控制面容器执行'
}

config_site_env_value() {
  local key="$1"
  [[ -f "$SITE_ENV_PATH" ]] || return 0
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^'\''|'\''$/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    }
  ' "$SITE_ENV_PATH"
}

config_emit_extension_env_fix_guidance() {
  local profile_id=''
  local check_output=''
  profile_id="$(config_site_env_value OPENCLAW_CONTROL_PLANE_PROFILE)"
  [[ -n "$profile_id" ]] || profile_id='agent_platform'
  echo "[one_click_config][HINT] 扩展 env 缺项检查：" >&2
  echo "  bash ./scripts/setup/check_extension_env_values.sh --profile $profile_id" >&2
  echo "[one_click_config][HINT] 按字段写入示例：" >&2
  echo "  bash ./scripts/setup/apply_extension_env_values.sh --profile $profile_id --init-from-example --set KEY=<value>" >&2
  echo "  export SECRET_KEY=<secret>; bash ./scripts/setup/apply_extension_env_values.sh --profile $profile_id --set-secret-from-env SECRET_KEY" >&2
  if config_run_and_capture check_output bash "$ROOT_DIR/scripts/setup/check_extension_env_values.sh" --profile "$profile_id"; then
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output" >&2
  else
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output" >&2
  fi
}

config_summary_command() {
  bash "$OPENCLAW_PYTHON_TOOL" setup flow config-summary "$@"
}

config_write_surface_summary() {
  local exit_code="$1"
  [[ "$DRY_RUN" == '1' || -z "$SUMMARY_JSON_PATH" ]] && return 0
  config_summary_command write-summary \
    --generated-at "$CONFIG_GENERATED_AT" \
    --output-path "$OUTPUT_PATH" \
    --failed-stage "$CONFIG_CURRENT_STAGE" \
    --exit-code "$exit_code" \
    --failure-message "$CONFIG_FAILURE_MESSAGE" \
    --dry-run "$DRY_RUN" \
    --out-json "$SUMMARY_JSON_PATH"
}

config_emit_surface_terminal() {
  local exit_code="$1"
  config_summary_command summary \
    --format text \
    --generated-at "$CONFIG_GENERATED_AT" \
    --output-path "$OUTPUT_PATH" \
    --failed-stage "$CONFIG_CURRENT_STAGE" \
    --exit-code "$exit_code" \
    --failure-message "$CONFIG_FAILURE_MESSAGE" \
    --dry-run "$DRY_RUN"
}

config_on_error() {
  local exit_code="$1"
  local helper_output=''
  trap - ERR
  [[ "$CONFIG_SUMMARY_EMITTED" == '1' ]] && exit "$exit_code"
  CONFIG_SUMMARY_EMITTED=1
  set +e
  if ! config_run_and_capture helper_output config_write_surface_summary "$exit_code"; then
    [[ -n "$helper_output" ]] && echo "$helper_output" >&2
  fi
  if ! config_run_and_capture helper_output config_emit_surface_terminal "$exit_code"; then
    [[ -n "$helper_output" ]] && echo "$helper_output" >&2
  else
    printf '%s\n' "$helper_output"
  fi
  exit "$exit_code"
}

config_assert_access_mode() {
  local path="$1"
  local mode="$2"
  local label="$3"
  case "$mode" in
    rx)
      [[ -r "$path" && -x "$path" ]] || config_fail "$label 缺少读取/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"
      ;;
    rw)
      [[ -r "$path" && -w "$path" ]] || config_fail "$label 缺少读取/写入权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"
      ;;
    rwx)
      [[ -r "$path" && -w "$path" && -x "$path" ]] || config_fail "$label 缺少读取/写入/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限。"
      ;;
    *)
      config_fail "未知权限模式：$mode"
      ;;
  esac
}

config_assert_dir_manageable_or_creatable() {
  local dir="$1"
  local label="$2"
  if [[ -d "$dir" ]]; then
    config_assert_access_mode "$dir" rwx "$label"
    return 0
  fi
  local parent_dir
  parent_dir="$(dirname "$dir")"
  [[ -d "$parent_dir" ]] || config_fail "$label 的父目录不存在：$parent_dir；当前脚本不会自动补建越级路径。"
  config_assert_access_mode "$parent_dir" rwx "$label 的父目录"
}

config_assert_file_manageable_or_creatable() {
  local file_path="$1"
  local label="$2"
  if [[ -e "$file_path" ]]; then
    [[ -f "$file_path" ]] || config_fail "$label 不是常规文件：$file_path"
    config_assert_access_mode "$file_path" rw "$label"
    return 0
  fi
  local parent_dir
  parent_dir="$(dirname "$file_path")"
  [[ -d "$parent_dir" ]] || config_fail "$label 的父目录不存在：$parent_dir；当前脚本不会自动补建越级路径。"
  config_assert_access_mode "$parent_dir" rwx "$label 的父目录"
}

config_check_local_permission_prereqs() {
  config_assert_access_mode "$ROOT_DIR" rx '仓库根目录'
  [[ -d "$ROOT_DIR/deploy" ]] && config_assert_access_mode "$ROOT_DIR/deploy" rx 'deploy 目录'
  if [[ -e "$SITE_ENV_PATH" ]]; then
    config_assert_file_manageable_or_creatable "$SITE_ENV_PATH" 'deploy/site.env'
  elif [[ "$DRY_RUN" != '1' ]]; then
    config_assert_file_manageable_or_creatable "$SITE_ENV_PATH" 'deploy/site.env'
  fi
  if [[ -e "$TARGETS_ENV_DIR" ]]; then
    [[ -d "$TARGETS_ENV_DIR" ]] || config_fail "deploy/targets.d 不是目录：$TARGETS_ENV_DIR"
    config_assert_access_mode "$TARGETS_ENV_DIR" rwx 'deploy/targets.d'
  elif [[ "$DRY_RUN" != '1' ]]; then
    config_assert_dir_manageable_or_creatable "$TARGETS_ENV_DIR" 'deploy/targets.d'
  fi
  if [[ -d "$ROOT_DIR/agent/extensions" ]]; then
    while IFS= read -r -d '' extension_env_path; do
      config_assert_file_manageable_or_creatable "$extension_env_path" 'extension.env'
    done < <(find "$ROOT_DIR/agent/extensions" -path '*/deploy/extension.env' -type f -print0 2>/dev/null)
  fi
  if [[ "$DRY_RUN" != '1' ]]; then
    config_assert_file_manageable_or_creatable "$OUTPUT_PATH" '输出 env 文件'
  fi
  if [[ "$DRY_RUN" != '1' && -n "$SUMMARY_JSON_PATH" ]]; then
    config_assert_file_manageable_or_creatable "$SUMMARY_JSON_PATH" 'config_summary.json'
  fi
}

config_check_runtime_user_prereqs() {
  if [[ "$(id -u)" == '0' && "${OPENCLAW_ALLOW_ROOT_RUNTIME_USER:-0}" != '1' ]]; then
    config_fail "one_click_config 拒绝以 root 生成 deploy/.env；请先执行 sudo bash ./scripts/setup/prepare_deploy_user.sh --user openclaw --repo-dir '$ROOT_DIR'，再切换到固定部署用户执行本脚本。若当前保留 root SSH 会话，可执行：runuser -u openclaw -- bash -lc 'cd $ROOT_DIR && bash ./scripts/setup/one_click_config.sh'。专用验证环境确需 root runtime 用户时，显式设置 OPENCLAW_ALLOW_ROOT_RUNTIME_USER=1。"
  fi
}

config_prepare_minimum_control_plane_layout() {
  [[ "$DRY_RUN" == '1' ]] && return 0
  runtime_permissions_prepare_control_plane_setup_layout "$ROOT_DIR"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || config_fail '--output 缺少路径参数'
      OUTPUT_PATH="$2"
      shift 2
      continue
      ;;
    --summary-json)
      [[ $# -ge 2 ]] || config_fail '--summary-json 缺少路径参数'
      SUMMARY_JSON_PATH="$2"
      shift 2
      continue
      ;;
    --site-env)
      [[ $# -ge 2 ]] || config_fail '--site-env 缺少路径参数'
      SITE_ENV_PATH="$2"
      shift 2
      continue
      ;;
    --targets-env-dir)
      [[ $# -ge 2 ]] || config_fail '--targets-env-dir 缺少路径参数'
      TARGETS_ENV_DIR="$2"
      shift 2
      continue
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      continue
      ;;
    --explain)
      EXPLAIN_ONLY=1
      shift
      continue
      ;;
    -h|--help)
      HELP_ONLY=1
      shift
      continue
      ;;
    *)
      flow_entry_handle_unknown_arg 'one_click_config' "$1" show_one_click_config_help
      exit 2
      ;;
  esac
done

if flow_entry_maybe_render_static_surface "$HELP_ONLY" "$EXPLAIN_ONLY" show_one_click_config_help show_one_click_config_explain; then
  exit 0
fi

CONFIG_CURRENT_STAGE='runtime_user_preflight'
config_check_runtime_user_prereqs
trap 'config_on_error $?' ERR

CONFIG_CURRENT_STAGE='control_plane_layout'
config_prepare_minimum_control_plane_layout
CONFIG_CURRENT_STAGE='local_permission_preflight'
config_check_local_permission_prereqs
RENDER_ARGS=(
  setup env
  render
  --output "$OUTPUT_PATH"
  --summary-json "$SUMMARY_JSON_PATH"
  --site-env "$SITE_ENV_PATH"
  --targets-env-dir "$TARGETS_ENV_DIR"
)
if [[ "$DRY_RUN" == '1' ]]; then
  RENDER_ARGS+=(--dry-run)
fi
CONFIG_CURRENT_STAGE='render_control_plane'
config_render_output=''
if ! config_run_and_capture config_render_output bash "$OPENCLAW_PYTHON_TOOL" "${RENDER_ARGS[@]}"; then
  if config_is_control_plane_preflight_output "$config_render_output"; then
    CONFIG_CURRENT_STAGE='control_plane_preflight'
  fi
  [[ -n "$config_render_output" ]] && echo "$config_render_output" >&2
  if [[ "$CONFIG_CURRENT_STAGE" != 'control_plane_preflight' ]]; then
    config_emit_extension_env_fix_guidance
  fi
  config_fail "setup env render 执行失败；请先按上述报错修正输入真源或控制面前置条件后，再重新执行 one_click_config.sh。"
fi
[[ -n "$config_render_output" ]] && printf '%s' "$config_render_output"
CONFIG_CURRENT_STAGE='effective_compose_render'
config_effective_compose_output=''
if [[ "$DRY_RUN" != '1' ]]; then
  EFFECTIVE_COMPOSE_PATH="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/docker-compose.effective.yml)"
  if ! config_run_and_capture config_effective_compose_output bash "$OPENCLAW_PYTHON_TOOL" runtime mounts sync-compose --output "$EFFECTIVE_COMPOSE_PATH"; then
    if config_is_control_plane_preflight_output "$config_effective_compose_output"; then
      CONFIG_CURRENT_STAGE='control_plane_preflight'
      config_fail "${config_effective_compose_output//$'
'/；}"
    fi
    [[ -n "$config_effective_compose_output" ]] && echo "$config_effective_compose_output" >&2
    config_fail "effective compose 生成失败；请按上述报错修正扩展服务块或 runtime mounts 真源后重试。"
  fi
  [[ -n "$config_effective_compose_output" ]] && printf '%s\n' "$config_effective_compose_output"
fi
CONFIG_CURRENT_STAGE='final_emit'
