#!/usr/bin/env python3
"""dispatch target 注册表的运行态渲染与摘要输出。"""
from __future__ import annotations

from typing import Any

from openclaw.lib.cli.examples import canonical_cli_command

from ._target_registry_shared import DISPATCH_GLOBAL_RUNTIME_ENV_NAMES, TARGET_MANAGED_ENV_FIELDS


def _format_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return ','.join(str(item) for item in value)
    return str(value)


def dispatch_runtime_env_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(env_value: object) -> None:
        value = str(env_value or '').strip()
        if not value or value in seen:
            return
        seen.add(value)
        names.append(value)

    targets = sorted(list(payload.get('targets') or []), key=lambda row: int(row.get('verificationOrderDefault') or 0))
    for target_item in targets:
        for field in TARGET_MANAGED_ENV_FIELDS:
            add(target_item.get(field))
    for runtime_env_name in DISPATCH_GLOBAL_RUNTIME_ENV_NAMES:
        add(runtime_env_name)
    return names


def build_dispatch_default_exports(payload: dict[str, Any]) -> list[str]:
    defaults = dict(payload.get('defaults') or {})
    targets = list(payload.get('targets') or [])
    lines = [
        f'DEFAULT_DEDUPE_WINDOW_HOURS={_format_env_value(defaults.get("dedupeWindowHours"))}',
        f'DEFAULT_MAX_ATTEMPTS={_format_env_value(defaults.get("maxAttempts"))}',
        f'DEFAULT_BACKOFF_SECONDS={_format_env_value(defaults.get("backoffSeconds"))}',
        f'DEFAULT_TARGET_MIN_INTERVAL_MS={_format_env_value(defaults.get("targetMinIntervalMs"))}',
        f'DEFAULT_TARGET_MAX_PER_SECOND={_format_env_value(defaults.get("targetMaxPerSecond"))}',
        f'DEFAULT_TARGET_MAX_PER_MINUTE={_format_env_value(defaults.get("targetMaxPerMinute"))}',
        f'DEFAULT_TARGET_RATE_LIMIT_STATE_TTL_SECONDS={_format_env_value(defaults.get("targetRateLimitStateTtlSeconds"))}',
        f'DISPATCH_DEDUPE_WINDOW_HOURS={_format_env_value(defaults.get("dedupeWindowHours"))}',
        f'DISPATCH_MAX_ATTEMPTS={_format_env_value(defaults.get("maxAttempts"))}',
        f'DISPATCH_BACKOFF_SECONDS={_format_env_value(defaults.get("backoffSeconds"))}',
        f'DISPATCH_TARGET_MIN_INTERVAL_MS={_format_env_value(defaults.get("targetMinIntervalMs"))}',
        f'DISPATCH_TARGET_MAX_PER_SECOND={_format_env_value(defaults.get("targetMaxPerSecond"))}',
        f'DISPATCH_TARGET_MAX_PER_MINUTE={_format_env_value(defaults.get("targetMaxPerMinute"))}',
        f'DISPATCH_TARGET_RATE_LIMIT_STATE_TTL_SECONDS={_format_env_value(defaults.get("targetRateLimitStateTtlSeconds"))}',
    ]
    ordered_targets = sorted(targets, key=lambda row: int(row.get('verificationOrderDefault') or 0))
    for target_item in ordered_targets:
        prefix = str(target_item.get('targetGroup') or '').strip().upper()
        boundary = target_item.get('boundary') if isinstance(target_item.get('boundary'), dict) else {}
        lines.extend([
            f'TARGET_GROUP_{prefix}_ENABLE_DEFAULT={_format_env_value(target_item.get("enabledDefault"))}',
            f'TARGET_GROUP_{prefix}_TITLE_DEFAULT={_format_env_value(target_item.get("titleDefault"))}',
            f'TARGET_GROUP_{prefix}_AT_ALL_DEFAULT={_format_env_value(target_item.get("atAllDefault"))}',
            f'TARGET_GROUP_{prefix}_MSG_FORMAT_DEFAULT={_format_env_value(target_item.get("formatDefault"))}',
            f'TARGET_GROUP_{prefix}_SILENCE_ENABLE_DEFAULT={_format_env_value(target_item.get("silenceEnabledDefault"))}',
            f'TARGET_GROUP_{prefix}_SILENCE_MIN_DELTA_DEFAULT={_format_env_value(target_item.get("silenceMinDeltaDefault"))}',
            f'TARGET_GROUP_{prefix}_ALLOWED_RELEASE_LEVELS_DEFAULT={_format_env_value(target_item.get("allowedReleaseLevelsDefault"))}',
            f'TARGET_GROUP_{prefix}_SECRET_REQUIRED_DEFAULT={_format_env_value(target_item.get("secretRequiredDefault"))}',
            f'TARGET_GROUP_{prefix}_DISPATCH_LANE_DEFAULT={_format_env_value(boundary.get("dispatchLane") or "")}',
            f'TARGET_GROUP_{prefix}_PAYLOAD_SCOPE_DEFAULT={_format_env_value(boundary.get("payloadScope") or "")}',
            f'TARGET_GROUP_{prefix}_PUBLISH_LATEST_DEFAULT={_format_env_value(boundary.get("publishLatestDefault"))}',
            f'{str(target_item.get("enabledEnv") or "").strip()}={_format_env_value(target_item.get("enabledDefault"))}',
            f'{str(target_item.get("titleEnv") or "").strip()}={_format_env_value(target_item.get("titleDefault"))}',
            f'{str(target_item.get("atAllEnv") or "").strip()}={_format_env_value(target_item.get("atAllDefault"))}',
            f'{str(target_item.get("formatEnv") or "").strip()}={_format_env_value(target_item.get("formatDefault"))}',
            f'{str(target_item.get("silenceEnabledEnv") or "").strip()}={_format_env_value(target_item.get("silenceEnabledDefault"))}',
            f'{str(target_item.get("silenceMinDeltaEnv") or "").strip()}={_format_env_value(target_item.get("silenceMinDeltaDefault"))}',
            f'{str(target_item.get("allowedReleaseLevelsEnv") or "").strip()}={_format_env_value(target_item.get("allowedReleaseLevelsDefault"))}',
        ])
    return [line for line in lines if '=' in line and not line.startswith('=')]


def build_dispatch_compose_env_block(payload: dict[str, Any], *, indent: str = '      ') -> list[str]:
    targets = sorted(list(payload.get('targets') or []), key=lambda target_row: int(target_row.get('verificationOrderDefault') or 0))

    def required_expr(var_name: str) -> str:
        return f'${{{var_name}?{var_name}_required}}'

    lines = [
        f'{indent}# target adapter / dispatch 默认块由 registry validator 校验、并由启用 extension 的 dispatchTargetRegistryPaths 定位的 target 注册表生成；修改真源后请执行：',
        f'{indent}#   {canonical_cli_command("setup", "env", "render-runtime-service-envs", "--env-file", "deploy/.env")}',
    ]
    for target in targets:
        for field in TARGET_MANAGED_ENV_FIELDS:
            env_name = str(target.get(field) or '').strip()
            lines.append(f"{indent}{env_name}: {required_expr(env_name)}")
    lines.extend([
        f'{indent}{env_name}: {required_expr(env_name)}'
        for env_name in DISPATCH_GLOBAL_RUNTIME_ENV_NAMES
    ])
    return lines


def build_dispatch_registry_summary(payload: dict[str, Any]) -> dict[str, Any]:
    targets = list(payload.get('targets') or [])
    release_policies = list(payload.get('releasePolicies') or [])
    lifecycle_states = list(payload.get('lifecycleStates') or [])
    verification_batches = list(((payload.get('verificationBatches') or {}).get('batches') or []))
    return {
        'registry_version': int(payload.get('version') or 0),
        'target_count': len(targets),
        'target_ids': [str(target_row.get('id') or '') for target_row in targets],
        'enabled_default_target_ids': [str(target_row.get('id') or '') for target_row in targets if bool(target_row.get('enabledDefault'))],
        'release_policy_count': len(release_policies),
        'release_policy_ids': [str(policy_row.get('id') or '') for policy_row in release_policies],
        'lifecycle_state_count': len(lifecycle_states),
        'lifecycle_state_ids': [str(state_row.get('id') or '') for state_row in lifecycle_states],
        'verification_batch_count': len(verification_batches),
        'verification_batch_ids': [str(batch_row.get('id') or '') for batch_row in verification_batches],
        'default_rotation_batch_id': str(((payload.get('verificationBatches') or {}).get('defaultRotationBatchId') or '')),
        'owners': {
            str(target_row.get('id') or ''): dict((target_row.get('owner') or {}))
            for target_row in targets
        },
        'target_roles': {
            str(target_row.get('id') or ''): {
                'title': str(target_row.get('titleDefault') or '').strip(),
                'display_name': str(target_row.get('displayName') or '').strip(),
                'role_description': str(target_row.get('roleDescription') or '').strip(),
                'audience_description': str(target_row.get('audienceDescription') or '').strip(),
                'boundary': dict(target_row.get('boundary') or {}),
            }
            for target_row in targets
        },
    }
