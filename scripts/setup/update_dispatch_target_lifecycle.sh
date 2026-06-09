#!/usr/bin/env bash
# 用途：按统一生命周期规则更新 dispatch target 注册表中的 lifecycleState，并对下线动作写出审计记录。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
PYTHON_RUNNER="${PYTHON_RUNNER:-$ROOT_DIR/scripts/runtime/run_python_container.sh}"
RUNTIME_PATHS_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
REGISTRY_PATH="${DISPATCH_TARGET_REGISTRY_PATH:-}"

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

resolve_dispatch_profile_for_target() {
  local target_id="$1"
bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" -- - "$target_id" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from openclaw.control_plane.registry import load_registry
from openclaw.lib.dispatch.target_registry import load_dispatch_registry
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.profiles import available_control_plane_profile_ids, resolve_control_plane_profile_service_config_path


target_id = sys.argv[1]
root_dir = resolve_repo_root(Path.cwd())
matches: list[tuple[str, Path]] = []
errors: list[str] = []
for profile_id in available_control_plane_profile_ids(root_dir):
    try:
        config_path = resolve_control_plane_profile_service_config_path(profile_id, start_path=root_dir)
        payload = load_registry(config_path)
        registry_paths = payload.get('registryPaths') if isinstance(payload.get('registryPaths'), dict) else {}
        target_paths = [
            Path(item).resolve()
            for item in list((registry_paths or {}).get('dispatchTargetRegistryPaths') or [])
            if str(item).strip()
        ]
        if not target_paths:
            continue
        provider_paths = [
            Path(item).resolve()
            for item in list((registry_paths or {}).get('dispatchProviderRegistryPaths') or [])
            if str(item).strip()
        ]
        merged = load_dispatch_registry(target_paths, provider_registry_path=provider_paths or None)
    except Exception as exc:
        errors.append(f'{profile_id}: {exc}')
        continue
    if any(isinstance(item, dict) and str(item.get('id') or '') == target_id for item in list(merged.get('targets') or [])):
        matches.append((profile_id, config_path))

if len(matches) == 1:
    print(matches[0][1])
    raise SystemExit(0)
if len(matches) > 1:
    ids = ', '.join(profile_id for profile_id, _path in matches)
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] target belongs to multiple dispatch profiles: {target_id} -> {ids}')
detail = f" ({'; '.join(errors[:3])})" if errors else ''
raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] no dispatch profile contains target: {target_id}{detail}')
PY
}

resolve_active_dispatch_config_path() {
  if [[ -n "$REQUESTED_CONFIG_PATH" ]]; then
    openclaw_control_plane_resolve_config_path agent_platform "$REQUESTED_CONFIG_PATH"
    return $?
  fi
  if [[ -n "$REQUESTED_PROFILE" ]]; then
    openclaw_control_plane_resolve_config_path "$REQUESTED_PROFILE" "" 1
    return $?
  fi
  if [[ -n "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" || -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]]; then
    openclaw_control_plane_resolve_config_path agent_platform
    return $?
  fi
  resolve_dispatch_profile_for_target "$TARGET_ID"
}

resolve_dispatch_target_registry_path() {
  local target_id="$1"
bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" -- - "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" "$target_id" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from openclaw.control_plane.registry import load_registry
from openclaw.lib.dispatch.target_registry import load_dispatch_registry


config_path = Path(sys.argv[1]).resolve()
target_id = sys.argv[2]
payload = load_registry(config_path)
paths = [
    Path(item).resolve()
    for item in list((payload.get('registryPaths') or {}).get('dispatchTargetRegistryPaths') or [])
    if str(item).strip()
]
if not paths:
    raise SystemExit('[update_dispatch_target_lifecycle][FAIL] active profile does not provide dispatchTargetRegistryPaths')
provider_paths = [
    Path(item).resolve()
    for item in list((payload.get('registryPaths') or {}).get('dispatchProviderRegistryPaths') or [])
    if str(item).strip()
]
merged = load_dispatch_registry(paths, provider_registry_path=provider_paths or None)
for item in list(merged.get('targets') or []):
    if not isinstance(item, dict) or str(item.get('id') or '') != target_id:
        continue
    source_path = str(item.get('sourceRegistryPath') or '').strip()
    if not source_path:
        raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] target has no sourceRegistryPath: {target_id}')
    print(Path(source_path).resolve())
    raise SystemExit(0)
raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] target does not exist: {target_id}')
PY
}

TARGET_ID=""
NEXT_STATE=""
WRITE_AUDIT=false
AUDIT_DIR=""
APPLY=false
QUIET=false
REQUESTED_CONFIG_PATH=""
REQUESTED_PROFILE=""

usage() {
  cat <<'USAGE'
用法：  bash ./scripts/setup/update_dispatch_target_lifecycle.sh --target <target_id> --state <active|disabled|decommissioned> [--config-path <path>|--control-plane-profile <profile_id>] [--apply] [--write-audit] [--audit-dir <path>] [--quiet]

说明：
  - 默认 dry-run，只输出拟变更摘要，不修改注册表。
  - 未显式指定 config 时，会按 profile registry 自动选择包含该 target 的 dispatch profile。
  - active -> disabled / decommissioned，disabled -> active / decommissioned 允许；decommissioned 默认不允许回退。
  - 进入 decommissioned 时，会自动把 enabledDefault 置为 false，并清空 verificationBatchIds。
  - --write-audit 默认落到 runtime_paths -> dispatch_target_lifecycle_audit_dir。

例子：
  bash ./scripts/setup/update_dispatch_target_lifecycle.sh --target <target_id> --state disabled --write-audit
  bash ./scripts/setup/update_dispatch_target_lifecycle.sh --target <target_id> --state disabled --apply --write-audit
  bash ./scripts/setup/update_dispatch_target_lifecycle.sh --target <target_id> --state decommissioned --apply --write-audit
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || { echo '[update_dispatch_target_lifecycle][FAIL] --target 缺少参数' >&2; exit 2; }
      TARGET_ID="$2"
      shift 2
      ;;
    --state)
      [[ $# -ge 2 ]] || { echo '[update_dispatch_target_lifecycle][FAIL] --state 缺少参数' >&2; exit 2; }
      NEXT_STATE="$2"
      shift 2
      ;;
    --apply)
      APPLY=true
      shift
      ;;
    --config-path)
      [[ $# -ge 2 ]] || { echo '[update_dispatch_target_lifecycle][FAIL] --config-path 缺少参数' >&2; exit 2; }
      REQUESTED_CONFIG_PATH="$2"
      shift 2
      ;;
    --control-plane-profile)
      [[ $# -ge 2 ]] || { echo '[update_dispatch_target_lifecycle][FAIL] --control-plane-profile 缺少参数' >&2; exit 2; }
      REQUESTED_PROFILE="$2"
      shift 2
      ;;
    --write-audit)
      WRITE_AUDIT=true
      shift
      ;;
    --audit-dir)
      [[ $# -ge 2 ]] || { echo '[update_dispatch_target_lifecycle][FAIL] --audit-dir 缺少参数' >&2; exit 2; }
      AUDIT_DIR="$2"
      shift 2
      ;;
    --quiet)
      QUIET=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[update_dispatch_target_lifecycle][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
done

runtime_paths_abs_host_path() {
  bash "$RUNTIME_PATHS_TOOL" \
    runtime paths resolve "$1" \
    --view host \
    --repo-root "$ROOT_DIR" \
    --config-path "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" \
    --abs-host
}

[[ -n "$TARGET_ID" ]] || { usage >&2; echo '[update_dispatch_target_lifecycle][FAIL] 必须提供 --target' >&2; exit 2; }
[[ -n "$NEXT_STATE" ]] || { usage >&2; echo '[update_dispatch_target_lifecycle][FAIL] 必须提供 --state' >&2; exit 2; }
if [[ -n "$REQUESTED_CONFIG_PATH" && -n "$REQUESTED_PROFILE" ]]; then
  echo '[update_dispatch_target_lifecycle][FAIL] --config-path 与 --control-plane-profile 只能二选一' >&2
  exit 2
fi
RESOLVED_CONTROL_PLANE_SERVICE_CONFIG_PATH="$(resolve_active_dispatch_config_path)"
export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$RESOLVED_CONTROL_PLANE_SERVICE_CONFIG_PATH"
DEFAULT_AUDIT_DIR="$(runtime_paths_abs_host_path dispatch_target_lifecycle_audit_dir)"
if [[ -z "$REGISTRY_PATH" ]]; then
  REGISTRY_PATH="$(resolve_dispatch_target_registry_path "$TARGET_ID")"
fi
[[ -f "$REGISTRY_PATH" ]] || { echo "[update_dispatch_target_lifecycle][FAIL] 缺少注册表：$REGISTRY_PATH" >&2; exit 3; }
if [[ "$WRITE_AUDIT" == "true" && -z "$AUDIT_DIR" ]]; then
  AUDIT_DIR="$DEFAULT_AUDIT_DIR"
fi

SUMMARY_JSON="$(bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" -- - "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" "$REGISTRY_PATH" "$TARGET_ID" "$NEXT_STATE" "$APPLY" "$WRITE_AUDIT" "$AUDIT_DIR" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.control_plane.registry import load_registry
from openclaw.lib.dispatch.target_registry import DispatchRegistryValidationError, load_dispatch_registry


config_path = Path(sys.argv[1]).resolve()
registry_path = Path(sys.argv[2]).resolve()
target_id = sys.argv[3]
next_state = sys.argv[4]
apply = sys.argv[5].lower() == 'true'
write_audit = sys.argv[6].lower() == 'true'
audit_dir = sys.argv[7]
allowed_states = {'active', 'disabled', 'decommissioned'}
if next_state not in allowed_states:
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] state 取值非法：{next_state}')

registry = load_registry(config_path)
registry_paths_root = registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {}
target_paths = [
    Path(item).resolve()
    for item in list((registry_paths_root or {}).get('dispatchTargetRegistryPaths') or [])
    if str(item).strip()
]
provider_paths = [
    Path(item).resolve()
    for item in list((registry_paths_root or {}).get('dispatchProviderRegistryPaths') or [])
    if str(item).strip()
]
if registry_path not in target_paths:
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] registry is not active under config: {registry_path}')

merged_payload = load_dispatch_registry(target_paths, provider_registry_path=provider_paths or None)
merged_rows = [
    item
    for item in list(merged_payload.get('targets') or [])
    if isinstance(item, dict)
]
merged_row = next((item for item in merged_rows if str(item.get('id') or '') == target_id), None)
if merged_row is None:
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] target 不存在：{target_id}')
resolved_source = Path(str(merged_row.get('sourceRegistryPath') or '')).resolve()
if resolved_source != registry_path:
    raise SystemExit(
        f'[update_dispatch_target_lifecycle][FAIL] target {target_id} belongs to {resolved_source}, not {registry_path}'
    )

source_payload = json.loads(registry_path.read_text(encoding='utf-8'))
rows = [item for item in list(source_payload.get('targets') or []) if isinstance(item, dict)]
row = next((item for item in rows if str(item.get('id') or '') == target_id), None)
if row is None:
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] target 不存在于源注册表：{target_id}')

current_state = str(row.get('lifecycleState') or '')
if current_state == 'decommissioned' and next_state != 'decommissioned':
    raise SystemExit('[update_dispatch_target_lifecycle][FAIL] decommissioned target 默认不允许回退；如需恢复，请先人工补齐 verificationBatchIds 与 owner/策略后再评审变更')

allowed_transitions = {
    'active': {'active', 'disabled', 'decommissioned'},
    'disabled': {'active', 'disabled', 'decommissioned'},
    'decommissioned': {'decommissioned'},
}
if next_state not in allowed_transitions.get(current_state, set()):
    raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] 不允许的生命周期迁移：{current_state} -> {next_state}')

merged_rows_by_id = {
    str(item.get('id') or ''): item
    for item in merged_rows
    if str(item.get('id') or '')
}
previous = {
    'lifecycleState': row.get('lifecycleState'),
    'enabledDefault': row.get('enabledDefault'),
    'verificationBatchIds': list(row.get('verificationBatchIds') or []),
}
target_group = str(row.get('targetGroup') or '')
changes = []
row['lifecycleState'] = next_state
if previous['lifecycleState'] != next_state:
    changes.append({'field': 'lifecycleState', 'before': previous['lifecycleState'], 'after': next_state})

if next_state == 'decommissioned':
    if row.get('enabledDefault') is not False:
        row['enabledDefault'] = False
        changes.append({'field': 'enabledDefault', 'before': previous['enabledDefault'], 'after': False})
    if list(row.get('verificationBatchIds') or []):
        row['verificationBatchIds'] = []
        changes.append({'field': 'verificationBatchIds', 'before': previous['verificationBatchIds'], 'after': []})

    batch_block = source_payload.get('verificationBatches') or {}
    batches = [item for item in list(batch_block.get('batches') or [])]
    next_batches = []
    for batch in batches:
        if not isinstance(batch, dict):
            next_batches.append(batch)
            continue
        batch_id = str(batch.get('id') or '')
        before_ids = list(batch.get('targetIds') or [])
        after_ids = [item for item in before_ids if item != target_id]
        if before_ids != after_ids:
            changes.append({'field': f'verificationBatches[{batch_id}].targetIds', 'before': before_ids, 'after': after_ids})
            batch['targetIds'] = after_ids
        before_groups = list(batch.get('requiredTargetGroups') or [])
        if target_group and target_group in before_groups:
            remaining_has_group = any(
                candidate_id != target_id
                and candidate_id in after_ids
                and str((merged_rows_by_id.get(candidate_id) or {}).get('targetGroup') or '') == target_group
                for candidate_id in after_ids
            )
            if not remaining_has_group:
                after_groups = [item for item in before_groups if item != target_group]
                if before_groups != after_groups:
                    changes.append({'field': f'verificationBatches[{batch_id}].requiredTargetGroups', 'before': before_groups, 'after': after_groups})
                    batch['requiredTargetGroups'] = after_groups
        if list(batch.get('targetIds') or []):
            next_batches.append(batch)
        else:
            changes.append({'field': 'verificationBatches.remove', 'before': batch_id, 'after': None})
    batch_block['batches'] = next_batches
    source_payload['verificationBatches'] = batch_block
elif next_state == 'disabled':
    if row.get('enabledDefault') is True:
        row['enabledDefault'] = False
        changes.append({'field': 'enabledDefault', 'before': previous['enabledDefault'], 'after': False})

with TemporaryDirectory() as tmp:
    staged_paths: list[Path] = []
    for path in target_paths:
        if path == registry_path:
            staged_path = Path(tmp) / path.name
            staged_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            staged_paths.append(staged_path)
        else:
            staged_paths.append(path)
    try:
        load_dispatch_registry(staged_paths, provider_registry_path=provider_paths or None)
    except DispatchRegistryValidationError as exc:
        raise SystemExit(f'[update_dispatch_target_lifecycle][FAIL] 变更后注册表校验失败：{exc}')

summary = {
    'schema_version': 1,
    'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'target_id': target_id,
    'apply': apply,
    'current_state': current_state,
    'next_state': next_state,
    'changes': changes,
    'registry_path': str(registry_path),
}
if apply:
    registry_path.write_text(json.dumps(source_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary['applied'] = True
else:
    summary['applied'] = False
if write_audit:
    base = Path(audit_dir)
    base.mkdir(parents=True, exist_ok=True)
    audit_path = base / f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}_{target_id}_{next_state}.json'
    audit_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary['audit_path'] = str(audit_path)
print(json.dumps(summary, ensure_ascii=False))
PY
)"

if [[ "$QUIET" != "true" ]]; then
  bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" -- - "$SUMMARY_JSON" <<'PY'
from __future__ import annotations

import json
import sys

summary = json.loads(sys.argv[1])
print(f"[update_dispatch_target_lifecycle] target={summary.get('target_id')} {summary.get('current_state')} -> {summary.get('next_state')} apply={summary.get('apply')} applied={summary.get('applied')}")
for row in summary.get('changes') or []:
    print(f"  - {row.get('field')}: {row.get('before')} -> {row.get('after')}")
if summary.get('audit_path'):
    print(f"[update_dispatch_target_lifecycle] audit={summary.get('audit_path')}")
PY
fi
