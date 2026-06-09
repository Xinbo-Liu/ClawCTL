#!/usr/bin/env bash
# 用途：导出交付包前执行本地残留洁净策略。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
OUTPUT_PATH=''
BUNDLE_ID='full-source-governance'
APPLY_CLEAN=0
CHECK_ONLY=0
CLEANLINESS_ONLY=0
QUIET=0
SIZE_MANIFEST_PATH=''
BOM_PATH=''
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
BUNDLE_MANIFEST_PATH=''
REPO_CONTRACTS_LOADED=0
LOCAL_WORKSPACE_POLICY_LOADED=0
TOOL_OVERLAY_DIR=''

cleanup() {
  [[ -n "$TOOL_OVERLAY_DIR" ]] || return 0
  case "$TOOL_OVERLAY_DIR" in
    "$ROOT_DIR/state/openclaw/control_plane/tmp/bundle-tools."*)
      rm -rf "$TOOL_OVERLAY_DIR"
      ;;
    *)
      echo "[export_clean_delivery_bundle][WARN] 拒绝清理非 bundle 工具目录：$TOOL_OVERLAY_DIR" >&2
      ;;
  esac
}
trap cleanup EXIT

ensure_repo_contracts_loaded() {
  if [[ "$REPO_CONTRACTS_LOADED" == '1' ]]; then
    return 0
  fi
  # shellcheck source=../lib/repo_contracts.sh
  source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
  repo_contract_assign_path BUNDLE_MANIFEST_PATH governance.bundle_manifest
  REPO_CONTRACTS_LOADED=1
}

ensure_local_workspace_policy_loaded() {
  if [[ "$LOCAL_WORKSPACE_POLICY_LOADED" == '1' ]]; then
    return 0
  fi
  # shellcheck source=../lib/local_workspace_policy.sh
  source "$ROOT_DIR/scripts/lib/local_workspace_policy.sh"
  LOCAL_WORKSPACE_POLICY_LOADED=1
}

usage() {
  cat <<'USAGE'
用法：bash ./scripts/setup/export_clean_delivery_bundle.sh [选项]

作用：
  1. 按统一本地残留策略扫描默认可清理目标与派生物
  2. 可选执行自动清理
  3. 按 bundle allowlist 导出规范交付包，并写出 size-manifest / BOM

选项：
  --bundle <runtime-core|ops-toolkit|full-source-governance>
                     指定导出包类型；默认 full-source-governance
  --output <path>    指定 zip 输出路径；默认按 bundle 前缀写到 ./tmp/
  --size-manifest <path>
                     指定 size manifest 输出路径；默认与 zip 同目录同名前缀
  --bom <path>       指定 BOM 输出路径；默认与 zip 同目录同名前缀
  --clean            导出前自动清理默认可清理目标与派生物
  --check-only       只检查工作树是否干净，并校验 bundle allowlist / 禁止路径 / 预算 / artifact smoke，不导出 zip
  --cleanliness-only 只执行本地残留洁净策略，不进入 bundle 规则校验，也不导出 zip
  --quiet            减少非必要输出
  -h, --help         显示帮助
USAGE
}

log() {
  [[ "$QUIET" == '1' ]] || echo "[export_clean_delivery_bundle] $*"
}

fail() {
  echo "[export_clean_delivery_bundle][FAIL] $*" >&2
  exit 2
}

assert_in_repo() {
  local target_path="$1"
  case "$target_path" in
    "$ROOT_DIR"/*) ;;
    *)
      fail "拒绝处理仓库外路径：$target_path"
      ;;
  esac
}

require_jq() {
  command -v jq >/dev/null 2>&1 || fail '缺少 jq；无法解析 bundle manifest 或治理真源。'
}

ensure_tool_overlay_dir() {
  if [[ -n "$TOOL_OVERLAY_DIR" ]]; then
    return 0
  fi
  mkdir -p "$ROOT_DIR/state/openclaw/control_plane/tmp"
  TOOL_OVERLAY_DIR="$(mktemp -d "$ROOT_DIR/state/openclaw/control_plane/tmp/bundle-tools.XXXXXX")"
  mkdir -p "$TOOL_OVERLAY_DIR/bin" "$TOOL_OVERLAY_DIR/lib"
}

copy_tool_runtime_deps() {
  local host_tool="$1"
  local ldd_output=""
  command -v ldd >/dev/null 2>&1 || return 0
  ldd_output="$(ldd "$host_tool" 2>/dev/null || true)"
  [[ -n "$ldd_output" ]] || return 0
  printf '%s\n' "$ldd_output" | awk '
    /=>[[:space:]]*\// { print $3; next }
    /^[[:space:]]*\// { print $1; next }
  ' | while IFS= read -r lib_path; do
    [[ -n "$lib_path" && -e "$lib_path" ]] || continue
    case "$(basename "$lib_path")" in
      linux-vdso*|ld-linux*|libc.so*|libm.so*|libpthread.so*|libdl.so*|librt.so*)
        continue
        ;;
    esac
    cp -L "$lib_path" "$TOOL_OVERLAY_DIR/lib/$(basename "$lib_path")"
  done
}

add_host_tool_overlay() {
  local tool_name="$1"
  local required="${2:-1}"
  local host_tool=""
  host_tool="$(command -v "$tool_name" || true)"
  if [[ -z "$host_tool" || ! -x "$host_tool" ]]; then
    if [[ "$required" == "1" ]]; then
      fail "缺少宿主机命令：$tool_name；请先执行 Docker host/base tools 准备。"
    fi
    return 0
  fi
  ensure_tool_overlay_dir
  cp -L "$host_tool" "$TOOL_OVERLAY_DIR/bin/$tool_name"
  chmod 755 "$TOOL_OVERLAY_DIR/bin/$tool_name"
  copy_tool_runtime_deps "$host_tool"
}

default_output_name() {
  local bundle_id="$1"
  local output_prefix=''
  local ts=''
  ensure_repo_contracts_loaded
  require_jq
  [[ -f "$BUNDLE_MANIFEST_PATH" ]] || fail "缺少 bundle manifest：$BUNDLE_MANIFEST_PATH"
  output_prefix="$(jq -r --arg bundle_id "$bundle_id" '.bundles[$bundle_id].outputPrefix // empty' "$BUNDLE_MANIFEST_PATH")"
  [[ -n "$output_prefix" ]] || output_prefix="${bundle_id//-/_}"
  ts="$(date -u '+%Y%m%d_%H%M%S')"
  printf '%s_%s.zip\n' "$output_prefix" "$ts"
}

collect_bundle_mount_dirs() {
  local raw_path=''
  local target_dir=''
  local -A seen=()
  for raw_path in "$@"; do
    [[ -n "$raw_path" ]] || continue
    target_dir="$(cd "$(dirname "$raw_path")" && pwd -P)"
    case "$target_dir" in
      "$ROOT_DIR"|"$ROOT_DIR"/*) ;;
      *)
        [[ -n "${seen[$target_dir]:-}" ]] && continue
        seen["$target_dir"]=1
        printf '%s\n' "$target_dir"
        ;;
    esac
  done
}

run_bundle_python() {
  local -a runner_args=(
    --workdir "$ROOT_DIR"
  )
  local mount_dir=''
  add_host_tool_overlay jq 1
  runner_args+=(--mount "$TOOL_OVERLAY_DIR")
  runner_args+=(--env "PATH=$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin")
  runner_args+=(--env "LD_LIBRARY_PATH=$TOOL_OVERLAY_DIR/lib")
  while IFS= read -r mount_dir; do
    [[ -n "$mount_dir" ]] || continue
    runner_args+=(--mount "$mount_dir")
  done < <(collect_bundle_mount_dirs "$OUTPUT_ABS" "$SIZE_MANIFEST_ABS" "$BOM_ABS")
  exec_or_run_static_python "${runner_args[@]}" -- "$@"
}

exec_or_run_static_python() {
  local -a runner_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --)
        shift
        break
        ;;
      *)
        runner_args+=("$1")
        shift
        ;;
    esac
  done
  bash "$STATIC_PYTHON_RUNNER" "${runner_args[@]}" -- "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)
      [[ $# -ge 2 ]] || fail '--bundle 缺少参数'
      BUNDLE_ID="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || fail '--output 缺少路径参数'
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --size-manifest)
      [[ $# -ge 2 ]] || fail '--size-manifest 缺少路径参数'
      SIZE_MANIFEST_PATH="$2"
      shift 2
      ;;
    --bom)
      [[ $# -ge 2 ]] || fail '--bom 缺少路径参数'
      BOM_PATH="$2"
      shift 2
      ;;
    --clean)
      APPLY_CLEAN=1
      shift
      ;;
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --cleanliness-only)
      CLEANLINESS_ONLY=1
      shift
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "不支持的参数：$1"
      ;;
  esac
done

if [[ "$CHECK_ONLY" == '1' && "$CLEANLINESS_ONLY" == '1' ]]; then
  fail '--check-only 与 --cleanliness-only 只能二选一'
fi

collect_dirty_paths() {
  if [[ "$CLEANLINESS_ONLY" == '1' || "${OPENCLAW_EXPORT_CLEANLINESS_SHELL_ONLY:-0}" == '1' || ! -f "$STATIC_PYTHON_RUNNER" ]]; then
    ensure_local_workspace_policy_loaded
    openclaw_local_workspace_policy_disposable_paths
    return $?
  fi
  exec_or_run_static_python -- - "$ROOT_DIR" <<'PY'
import json
import sys
from pathlib import Path

GLOB_CHARS = set('*?[]')


def fail(message: str) -> None:
    print(f'[export_clean_delivery_bundle][FAIL] {message}', file=sys.stderr)
    raise SystemExit(2)


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        fail(f'无法读取 JSON：{path} ({exc})')


def normalize_rel_path(root: Path, raw: object) -> str:
    normalized = str(raw or '').strip().replace('\\', '/').strip('/')
    if not normalized:
        fail('local_workspace_policy 路径不能为空')
    if normalized == '..' or normalized.startswith('../') or '/../' in normalized or normalized.endswith('/..'):
        fail(f'local_workspace_policy 路径越界：{raw}')
    try:
        (root / normalized).resolve().relative_to(root)
    except ValueError:
        fail(f'local_workspace_policy 路径越界：{raw}')
    return normalized


def contract_rel(root: Path, contract_id: str) -> Path:
    payload = read_json(root / 'config/governance/support/repo_contracts.json')
    contracts = payload.get('contracts') if isinstance(payload, dict) else None
    if not isinstance(contracts, list):
        fail('repo contracts truth 缺少 contracts')
    for item in contracts:
        if isinstance(item, dict) and str(item.get('id') or '').strip() == contract_id:
            rel = normalize_rel_path(root, item.get('relative_path'))
            return Path(rel)
    fail(f'未知 repo contract id：{contract_id}')


def host_state_root(root: Path) -> str:
    payload = read_json(root / contract_rel(root, 'governance.install_defaults'))
    defaults = payload.get('defaults') if isinstance(payload, dict) else None
    if not isinstance(defaults, dict):
        fail('install_defaults 缺少 defaults')
    return normalize_rel_path(root, defaults.get('host_state_root'))


def same_or_child(rel_path: str, parent: str) -> bool:
    child = str(rel_path or '').strip('/').replace('\\', '/')
    base = str(parent or '').strip('/').replace('\\', '/')
    return bool(child and base and (child == base or child.startswith(f'{base}/')))


def iter_derived(root: Path, pattern: object) -> list[str]:
    normalized = str(pattern or '').strip().replace('\\', '/')
    if not normalized:
        return []
    results: set[str] = set()
    if normalized in {'.coverage', '.coverage.*', 'tmp-*'}:
        for match in root.glob(normalized):
            results.add(match.relative_to(root).as_posix())
        return sorted(results)
    if normalized.endswith('/**'):
        anchor = normalized[:-3]
        if anchor.startswith('**/'):
            leaf = anchor[3:]
            if leaf and not any(ch in leaf for ch in GLOB_CHARS):
                for match in root.rglob(leaf):
                    results.add(match.relative_to(root).as_posix())
                return sorted(results)
        for match in root.glob(anchor):
            results.add(match.relative_to(root).as_posix())
        return sorted(results)
    if normalized in {'*.pyc', '*.pyo', '.DS_Store', 'Thumbs.db'}:
        return sorted({match.relative_to(root).as_posix() for match in root.rglob(normalized)})
    return sorted({match.relative_to(root).as_posix() for match in root.glob(normalized)})


root_dir = Path(sys.argv[1]).resolve()
policy = read_json(root_dir / contract_rel(root_dir, 'governance.local_workspace_policy'))
targets = policy.get('targets') if isinstance(policy, dict) else None
derived_globs = policy.get('derivedGlobs') if isinstance(policy, dict) else None
if not isinstance(targets, list) or not isinstance(derived_globs, list):
    fail('local_workspace_policy targets/derivedGlobs 非法')

policy_targets: list[str] = []
dirty: list[str] = []
for item in targets:
    if not isinstance(item, dict):
        fail('local_workspace_policy.targets 项必须为对象')
    truth_ref = str(item.get('truthRef') or '').strip()
    if truth_ref:
        if truth_ref != 'host_state_root':
            fail(f'local_workspace_policy.truthRef 非法：{truth_ref}')
        rel_path = host_state_root(root_dir)
    else:
        rel_path = normalize_rel_path(root_dir, item.get('path'))
    policy_targets.append(rel_path)
    if item.get('cleanupByDefault') is True and (root_dir / rel_path).exists():
        dirty.append(rel_path)

for pattern in derived_globs:
    for rel_path in iter_derived(root_dir, pattern):
        if any(same_or_child(rel_path, target) for target in policy_targets):
            continue
        dirty.append(rel_path)

for rel_path in sorted(dict.fromkeys(dirty)):
    print(rel_path)
PY
}

print_dirty_paths() {
  local paths="$1"
  [[ -n "$paths" ]] || return 0
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    rel_path="${rel_path%$'\r'}"
    printf '  - %s\n' "$rel_path"
  done <<< "$paths"
}

clean_dirty_paths() {
  local paths="$1"
  [[ -n "$paths" ]] || return 0
  local -a target_paths=()
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" ]] || continue
    rel_path="${rel_path%$'\r'}"
    local target_path="$ROOT_DIR/$rel_path"
    assert_in_repo "$target_path"
    [[ "$target_path" != "$ROOT_DIR" ]] || fail '拒绝删除仓库根目录'
    target_paths+=("$target_path")
  done <<< "$paths"
  (( ${#target_paths[@]} == 0 )) || rm -rf -- "${target_paths[@]}"
}

dirty_paths="$(collect_dirty_paths)"
if [[ -n "$dirty_paths" ]]; then
  log '检测到会污染交付包的默认可清理目标或派生物：'
  print_dirty_paths "$dirty_paths"
  if [[ "$APPLY_CLEAN" == '1' ]]; then
    log '开始清理默认可清理目标与派生物'
    clean_dirty_paths "$dirty_paths"
    dirty_paths=''
  fi
fi

if [[ -n "$dirty_paths" ]]; then
  fail '工作树仍不干净；请追加 --clean，或先手工清理后重试。'
fi

if [[ "$CLEANLINESS_ONLY" == '1' ]]; then
  log '工作树已通过本地残留洁净策略'
  exit 0
fi

if [[ -z "$OUTPUT_PATH" ]]; then
  default_name="$(default_output_name "$BUNDLE_ID")"
  OUTPUT_PATH="$ROOT_DIR/tmp/$default_name"
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"
OUTPUT_ABS="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)/$(basename "$OUTPUT_PATH")"
SIZE_MANIFEST_ABS=''
BOM_ABS=''
if [[ -n "$SIZE_MANIFEST_PATH" ]]; then
  mkdir -p "$(dirname "$SIZE_MANIFEST_PATH")"
  SIZE_MANIFEST_ABS="$(cd "$(dirname "$SIZE_MANIFEST_PATH")" && pwd)/$(basename "$SIZE_MANIFEST_PATH")"
fi
if [[ -n "$BOM_PATH" ]]; then
  mkdir -p "$(dirname "$BOM_PATH")"
  BOM_ABS="$(cd "$(dirname "$BOM_PATH")" && pwd)/$(basename "$BOM_PATH")"
fi

if [[ "$CHECK_ONLY" == '1' ]]; then
  log '工作树已通过干净度检查，开始校验 bundle 规则'
  run_bundle_python -m openclaw.release.bundle_governance validate --bundle "$BUNDLE_ID"
  exit 0
fi

build_cmd=(-m openclaw.release.bundle_governance build --bundle "$BUNDLE_ID" --output "$OUTPUT_ABS")
[[ -n "$SIZE_MANIFEST_ABS" ]] && build_cmd+=(--size-manifest "$SIZE_MANIFEST_ABS")
[[ -n "$BOM_ABS" ]] && build_cmd+=(--bom "$BOM_ABS")
log "开始按 allowlist 导出 bundle：$BUNDLE_ID"
run_bundle_python "${build_cmd[@]}"
