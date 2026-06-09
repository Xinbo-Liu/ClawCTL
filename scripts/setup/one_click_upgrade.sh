#!/usr/bin/env bash
# 用途：统一执行服务器部署升级主链，覆盖源码对齐、扩展依赖准备、服务启动与验收证据。
set -Eeuo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=scripts/setup/lib/runtime_permissions.sh
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=scripts/setup/lib/extension_env_gate.sh
source "$ROOT_DIR/scripts/setup/lib/extension_env_gate.sh"
# shellcheck source=scripts/lib/flow_step_runner.sh
source "$ROOT_DIR/scripts/lib/flow_step_runner.sh"

OPENCLAW_PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
ENV_FILE="$ROOT_DIR/deploy/.env"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
REPO_URL="${OPENCLAW_UPGRADE_REPO_URL:-}"
REF="${OPENCLAW_UPGRADE_REF:-main}"
COMMIT=""
OFFLINE_SOURCE_ARCHIVE=""
DRY_RUN=0
SKIP_SOURCE_SYNC=0
REFRESH_STACK_LOCK=0
MAINTENANCE_ENABLED=0
TARGET_COMMIT=""
TARGET_BASE_REPO=""
TARGET_BASE_TAG=""
HOST_STATE_ROOT="$(runtime_permissions_host_state_root "$ROOT_DIR")"
UPGRADE_ROOT="$HOST_STATE_ROOT/control_plane/upgrade"
BACKUP_DIR="$UPGRADE_ROOT/backups/$RUN_ID"
LOG_PATH="$UPGRADE_ROOT/one_click_upgrade.$RUN_ID.log"
SOURCE_METADATA_PATH="$UPGRADE_ROOT/source_sync_metadata.json"
CURRENT_STAGE_NAME="init"
LAST_FAILED_STEP=""
LAST_FAILED_CODE=0

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/one_click_upgrade.sh [选项]

选项：
  --repo-url URL                 远端 Git 仓库；非 Git 物化目录必须显式提供，或设置 OPENCLAW_UPGRADE_REPO_URL
  --ref main                     远端分支/标签；默认 main，或读取 OPENCLAW_UPGRADE_REF
  --commit SHA                   指定目标 commit
  --offline-source-archive TAR   使用离线源码 tar，而不是 git 拉取
  --dry-run                      只做读取、备份和报告，不覆盖源码、不启动服务
  --skip-source-sync             跳过源码同步，仅治理当前目录
  --refresh-stack-lock           显式刷新 openclaw-stack.lock.json；默认遇到 lock drift 只阻断
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[one_click_upgrade][FAIL] $*" >&2
  return 2
}

log() {
  printf '%s\n' "$*" | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
}

upgrade_run_logged_step() {
  flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE "$@"
}

upgrade_run_redacted_file_step() {
  local stage_name="$1"
  local output_path="$2"
  shift 2
  flow_set_var CURRENT_STAGE_NAME "$stage_name"
  log "[STEP] $stage_name"
  set +e
  "$@" 2>&1 | flow_redact_sensitive_stream >"$output_path"
  local exit_code=${PIPESTATUS[0]}
  set -e
  if [[ "$exit_code" -eq 0 ]]; then
    log "[OK] $stage_name"
    return 0
  fi
  flow_set_var LAST_FAILED_STEP "$stage_name"
  flow_set_var LAST_FAILED_CODE "$exit_code"
  log "[FAIL] $stage_name (exit=$exit_code)"
  return "$exit_code"
}

env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 1
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
  ' "$ENV_FILE"
}

assert_upgrade_runtime_user() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  [[ -f "$ENV_FILE" ]] || return 0
  local expected_uid=''
  local expected_gid=''
  expected_uid="$(env_value OPENCLAW_RUNTIME_UID 2>/dev/null || true)"
  expected_gid="$(env_value OPENCLAW_RUNTIME_GID 2>/dev/null || true)"
  [[ -n "$expected_uid" && -n "$expected_gid" ]] || return 0
  if [[ "$(id -u):$(id -g)" != "$expected_uid:$expected_gid" ]]; then
    fail "当前执行用户 $(id -u):$(id -g) 与 deploy/.env 的 OPENCLAW_RUNTIME_UID/GID=$expected_uid:$expected_gid 不一致；请切换到固定部署用户后执行：runuser -u openclaw -- bash -lc 'cd $ROOT_DIR && bash ./scripts/setup/one_click_upgrade.sh <原参数>'"
  fi
}

assert_upgrade_repo_writable() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  local blocked=''
  blocked="$(find "$ROOT_DIR/scripts" "$ROOT_DIR/python" -type f ! -writable -print -quit 2>/dev/null || true)"
  if [[ -n "$blocked" ]]; then
    fail "当前部署用户无法写入仓库文件：$blocked；请先用 root 执行 sudo chown -R $(id -un):$(id -gn) '$ROOT_DIR'，再重新执行升级。"
  fi
}

write_simple_report() {
  local path="$1"
  local status="$2"
  local message="${3:-}"
  local source_metadata_path=''
  [[ -f "$SOURCE_METADATA_PATH" ]] && source_metadata_path="$SOURCE_METADATA_PATH"
  mkdir -p "$(dirname "$path")"
  jq -n \
    --arg runId "$RUN_ID" \
    --arg status "$status" \
    --arg message "$message" \
    --arg targetBaseRepo "$TARGET_BASE_REPO" \
    --arg targetBaseTag "$TARGET_BASE_TAG" \
    --arg targetCommit "$TARGET_COMMIT" \
    --arg sourceMetadataPath "$source_metadata_path" '
      {
        schemaVersion: 1,
        runId: $runId,
        status: $status,
        message: $message,
        targetCommit: $targetCommit,
        targetBaseRepo: $targetBaseRepo,
        targetBaseTag: $targetBaseTag,
        sourceMetadataPath: $sourceMetadataPath,
        autoFixes: [],
        blockingIssues: (if ($status == "ok" or $status == "skipped") then [] else [{message: $message}] end),
        nextActions: []
      }
    ' \
    >"$path"
}

maintenance_file() {
  runtime_permissions_host_control_plane_file "$ROOT_DIR" scheduler_maintenance.json
}

write_maintenance() {
  local enabled="$1"
  local reason="$2"
  local path
  path="$(maintenance_file)"
  mkdir -p "$(dirname "$path")"
  cat >"$path" <<EOF
{
  "schemaVersion": 1,
  "enabled": $enabled,
  "reason": "$reason",
  "runId": "$RUN_ID",
  "updatedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  if [[ "$enabled" == "true" ]]; then
    MAINTENANCE_ENABLED=1
  else
    MAINTENANCE_ENABLED=0
  fi
}

enable_maintenance() {
  write_maintenance true "$1"
  log "[OK] scheduler maintenance enabled：$(maintenance_file)"
}

disable_maintenance() {
  write_maintenance false "$1"
  log "[OK] scheduler maintenance disabled：$(maintenance_file)"
}

on_error() {
  local exit_code=$?
  trap - ERR
  if [[ "$MAINTENANCE_ENABLED" == "1" ]]; then
    write_maintenance true "upgrade_failed"
    echo "[one_click_upgrade][WARN] 升级失败，scheduler maintenance 已保持 enabled；恢复命令：bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane scheduler-runtime maintenance disable --json" >&2
  fi
  write_simple_report "$UPGRADE_ROOT/upgrade_result.json" failed "one_click_upgrade failed at exit=$exit_code" || true
  exit "$exit_code"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      [[ $# -ge 2 ]] || fail '--repo-url 缺少参数'
      REPO_URL="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || fail '--ref 缺少参数'
      REF="$2"
      shift 2
      ;;
    --commit)
      [[ $# -ge 2 ]] || fail '--commit 缺少参数'
      COMMIT="$2"
      shift 2
      ;;
    --offline-source-archive)
      [[ $# -ge 2 ]] || fail '--offline-source-archive 缺少参数'
      OFFLINE_SOURCE_ARCHIVE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-source-sync)
      SKIP_SOURCE_SYNC=1
      shift
      ;;
    --refresh-stack-lock)
      REFRESH_STACK_LOCK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      exit 2
      ;;
  esac
done

assert_upgrade_runtime_user
assert_upgrade_repo_writable
mkdir -p "$UPGRADE_ROOT" "$BACKUP_DIR"
rm -f "$SOURCE_METADATA_PATH"
: >"$LOG_PATH"
trap on_error ERR

backup_current_state() {
  log "[STEP] backup"
  {
    printf '%s\n' "deploy/site.env"
    printf '%s\n' "deploy/.env"
    printf '%s\n' "deploy/targets.d"
    printf '%s\n' "deploy/nginx/certs"
    printf '%s\n' "deploy/certs"
    printf '%s\n' "deploy/secrets"
    printf '%s\n' "certs"
    printf '%s\n' "logs"
    printf '%s\n' "state"
    find "$ROOT_DIR/agent/extensions" -path '*/deploy/extension.env' -type f -printf '%P\n' 2>/dev/null | sed 's#^#agent/extensions/#' || true
  } | awk '!seen[$0]++' >"$BACKUP_DIR/protected_paths.txt"
  tar -C "$ROOT_DIR" -czf "$BACKUP_DIR/protected_files.tgz" --ignore-failed-read \
    --exclude state/openclaw/control_plane/upgrade \
    --exclude state/openclaw/control_plane/logs \
    --exclude state/openclaw/control_plane/tmp \
    deploy/site.env deploy/.env deploy/targets.d deploy/nginx/certs deploy/certs deploy/secrets certs logs state \
    agent/extensions/*/deploy/extension.env >/dev/null 2>&1 || true
  if git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$ROOT_DIR" rev-parse HEAD >"$BACKUP_DIR/source_commit.txt"
  else
    printf 'materialized_directory\n' >"$BACKUP_DIR/source_commit.txt"
  fi
  write_simple_report "$UPGRADE_ROOT/backup_report.json" ok "backup created"
  log "[OK] backup：$BACKUP_DIR"
}

resolve_repo_url() {
  [[ -n "$REPO_URL" ]] && return 0
  if git -C "$ROOT_DIR" remote get-url origin >/dev/null 2>&1; then
    REPO_URL="$(git -C "$ROOT_DIR" remote get-url origin)"
  fi
  [[ -n "$REPO_URL" ]] || fail '当前目录不是 Git 工作树，且未提供 --repo-url / OPENCLAW_UPGRADE_REPO_URL'
}

stack_lock_value() {
  local key="$1"
  jq -r --arg key "$key" '.base[$key] // ""' "$ROOT_DIR/openclaw-stack.lock.json" 2>/dev/null || true
}

resolve_stack_lock_base_metadata() {
  [[ -n "$TARGET_BASE_REPO" ]] || TARGET_BASE_REPO="${REPO_URL:-$(stack_lock_value repo)}"
  if [[ -z "$TARGET_COMMIT" ]]; then
    if [[ -n "$COMMIT" ]]; then
      TARGET_COMMIT="$COMMIT"
    elif git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
      TARGET_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD)"
    fi
  fi
}

prepare_source_tree() {
  local tmp_dir="$1"
  local source_dir="$tmp_dir/source"
  local is_offline_archive=0
  if [[ -n "$OFFLINE_SOURCE_ARCHIVE" ]]; then
    is_offline_archive=1
    [[ -f "$OFFLINE_SOURCE_ARCHIVE" ]] || {
      fail "离线源码包不存在：$OFFLINE_SOURCE_ARCHIVE"
      return $?
    }
    mkdir -p "$source_dir"
    tar -xf "$OFFLINE_SOURCE_ARCHIVE" -C "$source_dir" --strip-components=1 || return $?
  else
    resolve_repo_url || return $?
    git clone --quiet --branch "$REF" --depth 1 "$REPO_URL" "$source_dir" || return $?
  fi
  if [[ -n "$COMMIT" ]]; then
    if git -C "$source_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      git -C "$source_dir" fetch --quiet origin "$COMMIT" || true
      git -C "$source_dir" checkout --quiet "$COMMIT" || return $?
    else
      TARGET_COMMIT="$COMMIT"
    fi
  fi
  if git -C "$source_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    TARGET_COMMIT="$(git -C "$source_dir" rev-parse HEAD 2>/dev/null || true)"
  fi
  if [[ "$is_offline_archive" == "1" && -z "$TARGET_COMMIT" ]]; then
    fail '离线源码包不包含可解析 Git HEAD；使用 --offline-source-archive 时必须同时提供完整 40 位 --commit'
    return $?
  fi
  TARGET_BASE_REPO="${REPO_URL:-$TARGET_BASE_REPO}"
  TARGET_BASE_TAG="$(git -C "$source_dir" describe --tags --exact-match 2>/dev/null || true)"
  printf '%s\n' "$source_dir"
}

assert_current_tree_matches_target_commit() {
  git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  local head_commit untracked_path
  local -a pathspecs=(
    "."
    ":(exclude)openclaw-stack.lock.json"
    ":(exclude)config/control_plane/profile_registry.tsv"
    ":(exclude)deploy/.env"
    ":(exclude)deploy/site.env"
    ":(exclude).git/**"
    ":(exclude)agent/extensions/**"
    ":(exclude)artifacts/**"
    ":(exclude)certs/**"
    ":(exclude)deploy/certs/**"
    ":(exclude)deploy/nginx/certs/**"
    ":(exclude)deploy/secrets/**"
    ":(exclude)deploy/targets.d/**"
    ":(exclude)logs/**"
    ":(exclude)release/**"
    ":(exclude)state/**"
    ":(exclude)tmp/**"
    ":(glob,exclude)**/__pycache__/**"
    ":(glob,exclude)**/.pytest_cache/**"
    ":(glob,exclude)**/.mypy_cache/**"
    ":(glob,exclude)**/*.pyc"
    ":(glob,exclude)**/*.pyo"
  )
  head_commit="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$TARGET_COMMIT" && -n "$head_commit" && "$TARGET_COMMIT" != "$head_commit" ]]; then
    fail "跳过源码同步时声明的目标 commit 与当前 Git HEAD 不一致：$TARGET_COMMIT != $head_commit"
  fi
  if ! git -C "$ROOT_DIR" diff --quiet "$TARGET_COMMIT" -- "${pathspecs[@]}"; then
    fail '跳过源码同步时 base release 文件与目标 commit 不一致；否则无法把当前文件内容绑定到单一 base.commit'
    return $?
  fi
  while IFS= read -r untracked_path; do
    case "$untracked_path" in
      openclaw-stack.lock.json|config/control_plane/profile_registry.tsv|deploy/.env|deploy/site.env) continue ;;
      .git/*|agent/extensions/*|artifacts/*|certs/*|deploy/certs/*|deploy/nginx/certs/*|deploy/secrets/*|deploy/targets.d/*|logs/*|release/*|state/*|tmp/*) continue ;;
      */__pycache__/*|*/.pytest_cache/*|*/.mypy_cache/*|*.pyc|*.pyo) continue ;;
      *) fail "跳过源码同步时发现未纳入目标 commit 的 base release 文件：$untracked_path"; return $? ;;
    esac
  done < <(git -C "$ROOT_DIR" ls-files --others --exclude-standard 2>/dev/null || true)
}

base_release_should_hash_path() {
  local rel_path="$1"
  case "$rel_path" in
    openclaw-stack.lock.json|config/control_plane/profile_registry.tsv|deploy/.env|deploy/site.env)
      return 1
      ;;
    .git|.git/*|agent/extensions|agent/extensions/*|artifacts|artifacts/*|certs|certs/*|deploy/certs|deploy/certs/*|deploy/nginx/certs|deploy/nginx/certs/*|deploy/secrets|deploy/secrets/*|deploy/targets.d|deploy/targets.d/*|logs|logs/*|release|release/*|state|state/*|tmp|tmp/*)
      return 1
      ;;
    __pycache__/*|*/__pycache__/*|.pytest_cache/*|*/.pytest_cache/*|.mypy_cache/*|*/.mypy_cache/*|*.pyc|*.pyo)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

base_release_bundle_hash_for_dir() {
  local source_root="$1"
  [[ -d "$source_root" ]] || fail "无法计算 base release hash，目录不存在：$source_root"
  (
    cd "$source_root"
    find . -type f -print0 \
      | LC_ALL=C sort -z \
      | while IFS= read -r -d '' file_path; do
          local rel_path="${file_path#./}"
          base_release_should_hash_path "$rel_path" || continue
          printf '%s\0' "$rel_path"
          cat -- "$file_path"
          printf '\0'
        done
  ) | sha256sum | awk '{print $1}'
}

write_source_sync_metadata() {
  local metadata_root="${1:-$ROOT_DIR}"
  local release_bundle_hash=''
  resolve_stack_lock_base_metadata
  [[ -n "$TARGET_COMMIT" ]] || return 0
  [[ "$TARGET_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || fail "升级目标 commit 必须是完整 40 位 Git SHA，当前为：$TARGET_COMMIT"
  release_bundle_hash="$(base_release_bundle_hash_for_dir "$metadata_root")"
  mkdir -p "$(dirname "$SOURCE_METADATA_PATH")"
  jq -n \
    --arg runId "$RUN_ID" \
    --arg repo "$TARGET_BASE_REPO" \
    --arg commit "$TARGET_COMMIT" \
    --arg tag "$TARGET_BASE_TAG" \
    --arg releaseBundleHash "$release_bundle_hash" \
    --arg ref "$REF" '
      {
        schemaVersion: 1,
        generatedBy: "one_click_upgrade",
        runId: $runId,
        base: {
          repo: $repo,
          commit: $commit,
          tag: $tag,
          releaseBundleHash: $releaseBundleHash,
          ref: $ref
        }
      }
    ' >"$SOURCE_METADATA_PATH"
}

cleanup_source_tmp() {
  local tmp_dir="${1:-}"
  [[ -n "$tmp_dir" ]] || return 0
  case "$tmp_dir" in
    "$UPGRADE_ROOT"/source-sync.*)
      rm -rf "$tmp_dir"
      ;;
    *)
      fail "拒绝清理非升级临时源码目录：$tmp_dir"
      ;;
  esac
}

sync_source_tree() {
  local tmp_dir source_dir
  if [[ "$SKIP_SOURCE_SYNC" == "1" ]]; then
    write_source_sync_metadata "$ROOT_DIR"
    if [[ ! -f "$SOURCE_METADATA_PATH" ]] && ! git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
      fail '非 Git 物化目录使用 --skip-source-sync 时无法自动确定升级目标 commit；请提供完整 40 位 --commit，或执行源码同步。'
    fi
    assert_current_tree_matches_target_commit
    write_simple_report "$UPGRADE_ROOT/source_sync_report.json" skipped "source sync skipped"
    log "[SKIP] source sync"
    return 0
  fi
  log "[STEP] source sync"
  tmp_dir="$(mktemp -d "$UPGRADE_ROOT/source-sync.XXXXXX")"
  if ! source_dir="$(prepare_source_tree "$tmp_dir")"; then
    cleanup_source_tmp "$tmp_dir"
    fail 'source sync 获取目标源码失败；请确认 --repo-url 凭据、--ref/--commit 或 --offline-source-archive'
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    write_source_sync_metadata "$source_dir"
    write_simple_report "$UPGRADE_ROOT/source_sync_report.json" ok "dry-run resolved target source"
    log "[DRY-RUN] target commit=${TARGET_COMMIT:-unknown}"
    cleanup_source_tmp "$tmp_dir"
    return 0
  fi
  command -v rsync >/dev/null 2>&1 || fail '缺少 rsync，无法安全执行带保护清单的源码覆盖'
  rsync -a --delete \
    --exclude '/deploy/.env' \
    --exclude '/deploy/site.env' \
    --exclude '/deploy/targets.d/' \
    --exclude '/state/' \
    --exclude '/logs/' \
    --exclude '/certs/' \
    --exclude '/deploy/certs/' \
    --exclude '/deploy/nginx/certs/' \
    --exclude '/deploy/secrets/' \
    --exclude '/agent/extensions/*/deploy/extension.env' \
    "$source_dir/" "$ROOT_DIR/"
  write_source_sync_metadata "$ROOT_DIR"
  write_simple_report "$UPGRADE_ROOT/source_sync_report.json" ok "source synced"
  cleanup_source_tmp "$tmp_dir"
  log "[OK] source sync target=${TARGET_COMMIT:-unknown}"
}

verify_or_refresh_stack_lock() {
  local verify_status=''
  local -a lock_args=()
  local -a verify_args=(control-plane stack verify --strict-release --json)
  if [[ -f "$SOURCE_METADATA_PATH" ]]; then
    verify_args+=(--source-metadata "$SOURCE_METADATA_PATH")
  fi
  bash "$OPENCLAW_PYTHON_TOOL" "${verify_args[@]}" >"$UPGRADE_ROOT/stack_verify.json"
  verify_status="$(jq -r '.status // "fail"' "$UPGRADE_ROOT/stack_verify.json")"
  if [[ "$verify_status" == "ok" && "$REFRESH_STACK_LOCK" != "1" ]]; then
    return 0
  fi
  if [[ "$verify_status" != "ok" && "$REFRESH_STACK_LOCK" != "1" ]]; then
    fail 'control-plane stack verify 未通过；如已复核当前源码组合，请显式追加 --refresh-stack-lock 后再执行升级；若 issues 提示 base.commit 与 source metadata 不一致，说明源码已升级但 stack lock 仍指向旧基座 commit'
  fi
  resolve_stack_lock_base_metadata
  write_source_sync_metadata "$ROOT_DIR"
  if [[ ! -f "$SOURCE_METADATA_PATH" ]] && ! git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
    fail '当前目录不是 Git 工作树且没有 source sync target commit；跳过源码同步时刷新 stack lock 必须显式提供 --commit'
  fi
  lock_args=(control-plane stack lock)
  if [[ -f "$SOURCE_METADATA_PATH" ]]; then
    lock_args+=(--source-metadata "$SOURCE_METADATA_PATH" --update-source-provenance)
  else
    [[ -n "$TARGET_BASE_REPO" ]] && lock_args+=(--base-repo "$TARGET_BASE_REPO")
    [[ -n "$TARGET_COMMIT" ]] && lock_args+=(--base-commit "$TARGET_COMMIT")
    [[ -n "$TARGET_BASE_TAG" ]] && lock_args+=(--base-tag "$TARGET_BASE_TAG")
  fi
  bash "$OPENCLAW_PYTHON_TOOL" "${lock_args[@]}" >"$UPGRADE_ROOT/stack_lock_refresh.json"
  bash "$OPENCLAW_PYTHON_TOOL" "${verify_args[@]}" >"$UPGRADE_ROOT/stack_verify.json"
  verify_status="$(jq -r '.status // "fail"' "$UPGRADE_ROOT/stack_verify.json")"
  [[ "$verify_status" == "ok" ]] || fail '刷新 stack lock 后仍未通过 verify；请检查 stack_verify.json'
}

refresh_extension_lock_if_requested() {
  [[ "$REFRESH_STACK_LOCK" == "1" ]] || return 0
  bash "$OPENCLAW_PYTHON_TOOL" control-plane extensions lock >"$UPGRADE_ROOT/extensions_lock_refresh.json"
}

render_effective_compose() {
  local config_path="${1:-}"
  local effective_compose
  effective_compose="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/docker-compose.effective.yml)"
  mkdir -p "$(dirname "$effective_compose")"
  if [[ -n "$config_path" ]]; then
    bash "$OPENCLAW_PYTHON_TOOL" runtime mounts sync-compose --config-path "$config_path" --output "$effective_compose"
    bash "$OPENCLAW_PYTHON_TOOL" setup upgrade service-plan --config-path "$config_path" --compose-file "$effective_compose" --json >"$UPGRADE_ROOT/service_plan.json"
  else
    bash "$OPENCLAW_PYTHON_TOOL" runtime mounts sync-compose --output "$effective_compose"
    bash "$OPENCLAW_PYTHON_TOOL" setup upgrade service-plan --compose-file "$effective_compose" --json >"$UPGRADE_ROOT/service_plan.json"
  fi
}

ensure_extension_envs() {
  local config_path="$1"
  [[ -n "$config_path" && -f "$config_path" ]] || {
    write_simple_report "$UPGRADE_ROOT/extension_env_ensure.json" skipped "no active config path"
    return 0
  }
  extension_env_gate_ensure_active_profile \
    "$ROOT_DIR" \
    "$config_path" \
    "one_click_upgrade" \
    scheduler \
    "$UPGRADE_ROOT/extension_env_ensure.json"
}

write_service_start_report() {
  local raw_path="$UPGRADE_ROOT/service_start_report.jsonl"
  docker ps --format '{{json .}}' >"$raw_path"
  jq -s --arg runId "$RUN_ID" '
    {
      schemaVersion: 1,
      runId: $runId,
      status: "ok",
      containers: .
    }
  ' "$raw_path" >"$UPGRADE_ROOT/service_start_report.json"
}

runtime_services_all_healthy() {
  awk '
    /^== runtime target status ==/ { in_status=1; next }
    in_status && $1 == "target" { next }
    in_status && NF >= 4 {
      seen += 1
      if ($0 !~ /running healthy/) bad += 1
    }
    END { exit !(seen > 0 && bad == 0) }
  ' "$UPGRADE_ROOT/service_ready_status.txt"
}

wait_runtime_services_healthy() {
  local attempts="${OPENCLAW_UPGRADE_SERVICE_READY_ATTEMPTS:-18}"
  local sleep_seconds="${OPENCLAW_UPGRADE_SERVICE_READY_SLEEP_SECONDS:-10}"
  local attempt=1
  [[ "$attempts" =~ ^[0-9]+$ && "$attempts" -ge 1 ]] || fail 'OPENCLAW_UPGRADE_SERVICE_READY_ATTEMPTS 必须为 >=1 的整数'
  [[ "$sleep_seconds" =~ ^[0-9]+$ && "$sleep_seconds" -ge 1 ]] || fail 'OPENCLAW_UPGRADE_SERVICE_READY_SLEEP_SECONDS 必须为 >=1 的整数'
  while [[ "$attempt" -le "$attempts" ]]; do
    if bash "$ROOT_DIR/scripts/runtime/show_runtime_service_status.sh" --env-file "$ENV_FILE" >"$UPGRADE_ROOT/service_ready_status.txt" 2>&1 \
      && runtime_services_all_healthy; then
      log "[OK] runtime services healthy"
      return 0
    fi
    if [[ "$attempt" -lt "$attempts" ]]; then
      log "[INFO] runtime services 尚未全部 healthy，${sleep_seconds}s 后重试（${attempt}/${attempts}）"
      sleep "$sleep_seconds"
    fi
    attempt=$((attempt + 1))
  done
  cat "$UPGRADE_ROOT/service_ready_status.txt" | flow_redact_sensitive_stream >&2
  fail 'runtime services 未在等待窗口内全部进入 running healthy'
}

log "[START] one_click_upgrade run_id=$RUN_ID root=$ROOT_DIR"
backup_current_state
sync_source_tree

if [[ "$DRY_RUN" == "1" ]]; then
  bash "$ROOT_DIR/scripts/setup/fix_permissions.sh"
  bash "$OPENCLAW_PYTHON_TOOL" setup upgrade readiness --json >"$UPGRADE_ROOT/upgrade_readiness.latest.json"
  log "[DONE] dry-run 完成：$UPGRADE_ROOT"
  exit 0
fi

enable_maintenance "upgrade_started"
bash "$ROOT_DIR/scripts/setup/fix_permissions.sh"
bash "$OPENCLAW_PYTHON_TOOL" setup upgrade readiness --json >"$UPGRADE_ROOT/upgrade_readiness.latest.json"
refresh_extension_lock_if_requested
verify_or_refresh_stack_lock
bash "$OPENCLAW_PYTHON_TOOL" control-plane extensions doctor >"$UPGRADE_ROOT/extensions_doctor.txt"

CONFIG_PATH="$(env_value OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 2>/dev/null || true)"
render_effective_compose "$CONFIG_PATH"
ensure_extension_envs "$CONFIG_PATH"

upgrade_run_logged_step "deploy services" bash "$ROOT_DIR/scripts/setup/one_click_deploy.sh" --skip-acceptance
upgrade_run_logged_step "wait services healthy" wait_runtime_services_healthy
write_service_start_report

disable_maintenance "upgrade_acceptance_start"
upgrade_run_logged_step "run_all_once" bash "$ROOT_DIR/scripts/control_plane/run_control_plane_run_all_once.sh"
upgrade_run_redacted_file_step "full test" "$UPGRADE_ROOT/full_test_summary.json" bash "$ROOT_DIR/scripts/setup/one_click_test_full.sh" --env-file "$ENV_FILE" --json
upgrade_run_logged_step "runtime evidence" bash "$ROOT_DIR/scripts/runtime/export_runtime_acceptance_evidence.sh"
disable_maintenance "upgrade_complete"
write_simple_report "$UPGRADE_ROOT/upgrade_result.json" ok "upgrade completed"
log "[DONE] one_click_upgrade 完成：$UPGRADE_ROOT"
