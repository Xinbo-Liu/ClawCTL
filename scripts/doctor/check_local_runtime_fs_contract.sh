#!/usr/bin/env bash
# 用途：在不依赖 Docker / Python 容器的前提下，检查本地仓库、运行态目录、预创建输出目录与脚本执行位是否满足进入部署主链的前提。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/lib/image_env.sh"

ENV_FILE="$ROOT_DIR/deploy/.env"
REQUIRE_ENV_FILE=0
REQUIRE_CURRENT_RUNTIME_USER=0
REJECT_ROOT_RUNTIME_USER=0

FAILURES=0
WARNINGS=0
CURRENT_UID="$(id -u)"
CURRENT_GID="$(id -g)"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_local_runtime_fs_contract.sh [选项]

说明：
  - 只检查本地文件系统、权限边界、脚本执行位、runtime UID/GID 真源与部署前必须可写的预创建输出目录；不依赖 Docker。
  - 当前脚本不会自动 sudo、提权或 chown；只会报告缺口；fix_permissions 以 root 执行时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止。
  - 若仓库是通过 zip / scp / root 复制落地，建议先执行 bash ./scripts/setup/fix_permissions.sh，再回到本检查。
  - 若 deploy/.env 已声明 OPENCLAW_RUNTIME_UID / OPENCLAW_RUNTIME_GID，本检查会额外提示其与当前执行用户是否一致。

选项：
  --env-file <path>            覆盖默认 env 文件路径（默认：deploy/.env）
  --require-env-file           要求 env 文件必须已存在且可写
  --require-current-runtime-user
                              要求当前执行用户与 OPENCLAW_RUNTIME_UID/GID 完全一致
  --reject-root-runtime-user   拒绝 OPENCLAW_RUNTIME_UID/GID 为 0:0 的部署真源
  -h, --help                   显示帮助
USAGE
}

note() { printf '[INFO] %s\n' "$*"; }
warn() { WARNINGS=1; printf '[WARN] %s\n' "$*"; }
fail() { FAILURES=1; printf '[FAIL] %s\n' "$*" >&2; }

check_dir_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if runtime_permissions_assert_dir_manageable_or_creatable "$path" "$label"; then
    return 0
  fi
  fail "$label 不满足本地读写执行前提：$path"
}

check_file_manageable_or_creatable() {
  local path="$1"
  local label="$2"
  if runtime_permissions_assert_file_manageable_or_creatable "$path" "$label"; then
    return 0
  fi
  fail "$label 不满足本地读写前提：$path"
}

warn_owner_mismatch() {
  local path="$1"
  local label="$2"
  [[ -e "$path" ]] || return 0
  command -v stat >/dev/null 2>&1 || return 0
  local owner uid gid
  owner="$(stat -c '%u:%g' "$path" 2>/dev/null || true)"
  [[ -n "$owner" ]] || return 0
  uid="${owner%%:*}"
  gid="${owner##*:}"
  if [[ "$uid" != "$CURRENT_UID" || "$gid" != "$CURRENT_GID" ]]; then
    warn "$label 当前 owner 为 $owner，当前执行用户为 ${CURRENT_UID}:${CURRENT_GID}；若后续需要直接写入该路径，请先确认宿主机所有权合同。当前脚本不会自动 chown。"
  fi
}

check_lf_only_file() {
  local path="$1"
  local label="$2"
  [[ -f "$path" ]] || return 0
  if LC_ALL=C grep -q $'\r' "$path" 2>/dev/null; then
    fail "$label 包含 CRLF / 回车字符：$path；Linux 部署入口只接受 LF。请在提交或上传前执行 dos2unix，或使用 perl -pi -e 's/\\r$//' <file> 修复。"
  fi
}

check_line_endings_contract() {
  local path=""
  check_lf_only_file "$ROOT_DIR/.gitattributes" ".gitattributes"
  check_lf_only_file "$ENV_FILE" "env 文件"
  check_lf_only_file "$ROOT_DIR/deploy/site.env" "site env"
  if [[ -d "$ROOT_DIR/deploy/targets.d" ]]; then
    while IFS= read -r -d '' path; do
      check_lf_only_file "$path" "target env"
    done < <(find "$ROOT_DIR/deploy/targets.d" -maxdepth 1 -type f -name '*.env' -print0 2>/dev/null)
  fi
  if [[ -d "$ROOT_DIR/agent/extensions" ]]; then
    while IFS= read -r -d '' path; do
      check_lf_only_file "$path" "extension env"
    done < <(find "$ROOT_DIR/agent/extensions" -path '*/deploy/extension.env' -type f -print0 2>/dev/null)
  fi
  while IFS= read -r -d '' path; do
    check_lf_only_file "$path" "仓库可执行文本"
  done < <(runtime_permissions_collect_repo_exec_candidates "$ROOT_DIR")
}

check_runtime_identity_contract() {
  [[ -f "$ENV_FILE" ]] || return 0
  local runtime_uid='' runtime_gid=''
  runtime_uid="$(image_env_read_key_from_file_bootstrap "$ENV_FILE" OPENCLAW_RUNTIME_UID)"
  runtime_gid="$(image_env_read_key_from_file_bootstrap "$ENV_FILE" OPENCLAW_RUNTIME_GID)"
  if [[ -n "$runtime_uid" && ! "$runtime_uid" =~ ^[0-9]+$ ]]; then
    fail "OPENCLAW_RUNTIME_UID 不是整数：$runtime_uid"
    return 0
  fi
  if [[ -n "$runtime_gid" && ! "$runtime_gid" =~ ^[0-9]+$ ]]; then
    fail "OPENCLAW_RUNTIME_GID 不是整数：$runtime_gid"
    return 0
  fi
  [[ -n "$runtime_uid" && -n "$runtime_gid" ]] || return 0
  if [[ "$runtime_uid" == "0" || "$runtime_gid" == "0" ]]; then
    if [[ "$REJECT_ROOT_RUNTIME_USER" == "1" ]]; then
      fail "deploy/.env 当前声明 runtime UID/GID=${runtime_uid}:${runtime_gid}；one_click 主链拒绝 root runtime 用户。请切换到固定部署用户后重新执行 one_click_config.sh，或仅在明确接受风险的专用验证环境中单独运行本 doctor。"
    else
      warn "deploy/.env 当前声明 runtime UID/GID=${runtime_uid}:${runtime_gid}；这会把至少一部分 runtime 服务提升到 root 身份。当前仓库允许显式声明，但不建议把它作为长期默认值。"
    fi
  fi
  if [[ "$runtime_uid" != "$CURRENT_UID" || "$runtime_gid" != "$CURRENT_GID" ]]; then
    if [[ "$REQUIRE_CURRENT_RUNTIME_USER" == "1" ]]; then
      local runtime_user=''
      runtime_user="$(getent passwd "$runtime_uid" 2>/dev/null | cut -d: -f1 || true)"
      if [[ -n "$runtime_user" ]]; then
        fail "当前执行用户 ${CURRENT_UID}:${CURRENT_GID} 与 deploy/.env 的 OPENCLAW_RUNTIME_UID/GID=${runtime_uid}:${runtime_gid} 不一致；请切换到固定部署用户后重试。若当前保留 root SSH 会话，可执行：runuser -u $runtime_user -- bash -lc 'cd $ROOT_DIR && <原命令>'。"
      else
        fail "当前执行用户 ${CURRENT_UID}:${CURRENT_GID} 与 deploy/.env 的 OPENCLAW_RUNTIME_UID/GID=${runtime_uid}:${runtime_gid} 不一致；请切换到固定部署用户后重试。"
      fi
    else
      warn "deploy/.env 当前声明 runtime UID/GID=${runtime_uid}:${runtime_gid}，当前执行用户为 ${CURRENT_UID}:${CURRENT_GID}；fix_permissions 以 root 执行时必须能解析该 UID/GID，解析失败会中止。若继续使用不同 runtime 用户，必须先完成 bind mount owner/UID/GID 合同收口。"
    fi
  else
    note "runtime UID/GID 真源与当前执行用户一致：${runtime_uid}:${runtime_gid}"
  fi
}

check_manifest_and_compose_targets() {
  local action=""
  local path=""
  while IFS=$'\t' read -r action path; do
    [[ -n "$action" && -n "$path" ]] || continue
    case "$action" in
      dir)
        check_dir_manageable_or_creatable "$path" "runtime_paths bootstrap 目录"
        warn_owner_mismatch "$path" "runtime_paths bootstrap 目录"
        ;;
      parent)
        check_dir_manageable_or_creatable "$path" "runtime_paths bootstrap 父目录"
        warn_owner_mismatch "$path" "runtime_paths bootstrap 父目录"
        ;;
      *) fail "检测到未知 runtime_paths bootstrap 动作：$action -> $path" ;;
    esac
  done < <(runtime_permissions_collect_manifest_host_bootstrap_entries "$ROOT_DIR")

  while IFS=$'\t' read -r action path; do
    [[ -n "$action" && -n "$path" ]] || continue
    case "$action" in
      dir)
        check_dir_manageable_or_creatable "$path" "compose bind 宿主机目录"
        warn_owner_mismatch "$path" "compose bind 宿主机目录"
        ;;
      parent)
        check_dir_manageable_or_creatable "$path" "compose bind 宿主机父目录"
        warn_owner_mismatch "$path" "compose bind 宿主机父目录"
        ;;
      *) fail "检测到未知 compose bind 动作：$action -> $path" ;;
    esac
  done < <(runtime_permissions_collect_compose_host_bind_targets "$ROOT_DIR")
}

check_optional_image_pull_state() {
  local image_pull_dir="$ROOT_DIR/state/image_pull"
  local file=""
  check_dir_manageable_or_creatable "$ROOT_DIR/state" "镜像状态父目录"
  if [[ -e "$image_pull_dir" && ! -d "$image_pull_dir" ]]; then
    fail "镜像拉取状态路径不是目录：$image_pull_dir"
    return 0
  fi
  if [[ -d "$image_pull_dir" ]]; then
    check_dir_manageable_or_creatable "$image_pull_dir" "镜像拉取状态目录"
    warn_owner_mismatch "$image_pull_dir" "镜像拉取状态目录"
  fi
  for file in \
    "$image_pull_dir/cleanup_aliases.log" \
    "$image_pull_dir/pull_records.log" \
    "$image_pull_dir/pulled_images.txt"; do
    [[ -e "$file" ]] || continue
    check_file_manageable_or_creatable "$file" "镜像拉取状态文件"
    warn_owner_mismatch "$file" "镜像拉取状态文件"
  done
}

check_fixed_paths() {
  local host_state_dir=""
  local host_control_plane_dir=""
  host_state_dir="$(runtime_permissions_host_state_root "$ROOT_DIR")"
  host_control_plane_dir="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"

  runtime_permissions_assert_access_mode "$ROOT_DIR" rx "仓库根目录" || fail "仓库根目录缺少读取/执行权限：$ROOT_DIR"
  if [[ -d "$ROOT_DIR/deploy" ]]; then
    runtime_permissions_assert_access_mode "$ROOT_DIR/deploy" rx "deploy 目录" || true
  fi
  if [[ "$REQUIRE_ENV_FILE" == "1" ]]; then
    [[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
    check_file_manageable_or_creatable "$ENV_FILE" "env 文件"
  elif [[ -f "$ENV_FILE" ]]; then
    check_file_manageable_or_creatable "$ENV_FILE" "env 文件"
  fi
  check_dir_manageable_or_creatable "$ROOT_DIR/state" "state 目录"
  check_dir_manageable_or_creatable "$host_state_dir" "运行态 state 目录"
  warn_owner_mismatch "$host_state_dir" "运行态 state 目录"

  local dirs=(
    "$host_state_dir/tmp"
    "$host_state_dir/setup"
    "$host_control_plane_dir/dispatch"
    "$host_control_plane_dir/setup/official_cli"
    "$host_control_plane_dir/release/evidence"
    "$ROOT_DIR/state/image_artifacts"
    "$ROOT_DIR/release"
    "$ROOT_DIR/deploy/nginx/certs"
  )
  local dir=""
  for dir in "${dirs[@]}"; do
    check_dir_manageable_or_creatable "$dir" "固定目录"
    warn_owner_mismatch "$dir" "固定目录"
  done

  local files=(
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" deployment_acceptance.json)"
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/deployment_acceptance.json)"
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/one_click_test_full.latest.summary.json)"
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/one_click_test_full.latest.summary.md)"
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/one_click_deploy.latest.summary.json)"
    "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/one_click_deploy.latest.summary.md)"
  )
  local file=""
  for file in "${files[@]}"; do
    check_file_manageable_or_creatable "$file" "固定文件"
    warn_owner_mismatch "$file" "固定文件"
  done
  check_optional_image_pull_state
}

check_repo_exec_bits() {
  local file=""
  local missing=0
  while IFS= read -r -d '' file; do
    [[ -x "$file" ]] && continue
    printf '[FAIL] 缺少执行位：%s\n' "$file" >&2
    missing=1
  done < <(runtime_permissions_collect_repo_exec_candidates "$ROOT_DIR")
  if [[ "$missing" == "1" ]]; then
    FAILURES=1
    echo "[FAIL] 仓库内存在脚本执行位漂移；请先执行 bash ./scripts/setup/fix_permissions.sh，再重试。" >&2
  fi
}


while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || { echo '[FAIL] --env-file 缺少路径参数' >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --require-env-file)
      REQUIRE_ENV_FILE=1
      shift
      ;;
    --require-current-runtime-user)
      REQUIRE_CURRENT_RUNTIME_USER=1
      shift
      ;;
    --reject-root-runtime-user)
      REJECT_ROOT_RUNTIME_USER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

note '检查本地文件系统与权限合同'
check_runtime_identity_contract
check_fixed_paths
check_manifest_and_compose_targets
check_line_endings_contract
check_repo_exec_bits

if [[ "$FAILURES" == "1" ]]; then
  exit 2
fi
if [[ "$WARNINGS" == "1" ]]; then
  note '本地文件系统合同检查通过，但存在 owner/合同告警；建议在正式部署前收口。'
else
  note '本地文件系统合同检查通过。'
fi
