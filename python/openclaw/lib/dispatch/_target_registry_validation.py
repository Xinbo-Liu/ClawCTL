#!/usr/bin/env python3
"""dispatch target 注册表的结构校验与边界合同校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from openclaw.lib.channels.provider_registry import channel_provider_adapter_specs

from ._target_registry_shared import (
    DEFAULT_PROVIDER_REGISTRY_PATH,
    DispatchRegistryValidationError,
    TARGET_MANAGED_ENV_FIELDS,
    _default_provider_registry_paths,
    _index_by_id,
    _require_bool,
    _require_int,
    _require_keys,
    _require_list,
    _require_non_empty_text,
    _require_object,
    _require_unique_text_list,
    load_dispatch_registry_schema,
)


def _validate_dispatch_defaults(payload: dict[str, Any], *, schema_payload: dict[str, Any]) -> None:
    defaults = _require_object(payload.get('defaults'), label='dispatch target 注册表.defaults')
    _require_keys(defaults, list(schema_payload.get('defaultsRequired') or []), label='dispatch target 注册表.defaults')
    _require_int(defaults.get('dedupeWindowHours'), label='dispatch target 注册表.defaults.dedupeWindowHours', minimum=1)
    _require_int(defaults.get('maxAttempts'), label='dispatch target 注册表.defaults.maxAttempts', minimum=1)
    backoff_seconds = _require_unique_text_list(
        [str(item) for item in _require_list(defaults.get('backoffSeconds'), label='dispatch target 注册表.defaults.backoffSeconds')],
        label='dispatch target 注册表.defaults.backoffSeconds',
        allow_empty=False,
    )
    for idx, text in enumerate(backoff_seconds):
        try:
            if int(text) < 1:
                raise ValueError
        except Exception as exc:
            raise DispatchRegistryValidationError(f'dispatch target 注册表.defaults.backoffSeconds[{idx}] 必须为正整数') from exc
    _require_int(defaults.get('targetMinIntervalMs'), label='dispatch target 注册表.defaults.targetMinIntervalMs', minimum=0)
    _require_int(defaults.get('targetMaxPerSecond'), label='dispatch target 注册表.defaults.targetMaxPerSecond', minimum=1)
    _require_int(defaults.get('targetMaxPerMinute'), label='dispatch target 注册表.defaults.targetMaxPerMinute', minimum=1)
    _require_int(
        defaults.get('targetRateLimitStateTtlSeconds'),
        label='dispatch target 注册表.defaults.targetRateLimitStateTtlSeconds',
        minimum=1,
    )


def _validate_dispatch_registry_version(payload: dict[str, Any], *, schema_payload: dict[str, Any]) -> None:
    version = _require_int(payload.get('version'), label='dispatch target 注册表.version', minimum=1)
    minimum_version = _require_int(
        schema_payload.get('minimumRegistryVersion'),
        label='dispatch target schema.minimumRegistryVersion',
        minimum=1,
    )
    if version < minimum_version:
        raise DispatchRegistryValidationError(f'dispatch target 注册表版本过旧：{version}；当前最小版本要求：{minimum_version}')


def _build_dispatch_validation_contract(
    schema_payload: dict[str, Any],
    *,
    provider_registry_path: Path | Sequence[Path] | None,
) -> dict[str, Any]:
    provider_specs = channel_provider_adapter_specs(
        provider_registry_path or DEFAULT_PROVIDER_REGISTRY_PATH or _default_provider_registry_paths()
    )
    allowed_target_transports = {transport for _, transport in provider_specs.keys()}
    allowed_target_providers = {provider for provider, _ in provider_specs.keys()}
    if not allowed_target_transports or not allowed_target_providers:
        raise DispatchRegistryValidationError('dispatch provider adapter 注册表不能为空')
    return {
        'schema_payload': schema_payload,
        'provider_specs': provider_specs,
        'allowed_target_transports': allowed_target_transports,
        'allowed_target_providers': allowed_target_providers,
        'allowed_target_groups': set(
            _require_unique_text_list(schema_payload.get('allowedTargetGroups'), label='dispatch target schema.allowedTargetGroups')
        ),
        'allowed_delivery_tiers': set(
            _require_unique_text_list(schema_payload.get('allowedDeliveryTiers'), label='dispatch target schema.allowedDeliveryTiers')
        ),
        'allowed_message_profiles': set(
            _require_unique_text_list(schema_payload.get('allowedMessageProfiles'), label='dispatch target schema.allowedMessageProfiles')
        ),
        'allowed_dispatch_lanes': set(
            _require_unique_text_list(schema_payload.get('allowedDispatchLanes'), label='dispatch target schema.allowedDispatchLanes')
        ),
        'allowed_payload_scopes': set(
            _require_unique_text_list(schema_payload.get('allowedPayloadScopes'), label='dispatch target schema.allowedPayloadScopes')
        ),
        'allowed_formats': set(
            _require_unique_text_list(schema_payload.get('allowedMessageFormats'), label='dispatch target schema.allowedMessageFormats')
        ),
        'allowed_release_levels': set(
            _require_unique_text_list(schema_payload.get('allowedReleaseLevels'), label='dispatch target schema.allowedReleaseLevels')
        ),
        'target_group_boundary_rules': _require_object(
            schema_payload.get('targetGroupBoundaryRules'),
            label='dispatch target schema.targetGroupBoundaryRules',
        ),
    }


def _validate_release_policy_rows(
    payload: dict[str, Any],
    *,
    schema_payload: dict[str, Any],
    allowed_release_levels: set[str],
) -> dict[str, dict[str, Any]]:
    release_policies = _require_list(payload.get('releasePolicies'), label='dispatch target 注册表.releasePolicies')
    release_policy_index = _index_by_id(release_policies, label='dispatch target 注册表.releasePolicies')
    for policy_id, row in release_policy_index.items():
        _require_keys(
            row,
            list(schema_payload.get('requiredReleasePolicyFields') or []),
            label=f'dispatch target 注册表.releasePolicies[{policy_id}]',
        )
        _require_non_empty_text(row.get('title'), label=f'dispatch target 注册表.releasePolicies[{policy_id}].title')
        _require_non_empty_text(row.get('description'), label=f'dispatch target 注册表.releasePolicies[{policy_id}].description')
        allowed_levels = _require_unique_text_list(
            row.get('allowedReleaseLevels'),
            label=f'dispatch target 注册表.releasePolicies[{policy_id}].allowedReleaseLevels',
            allowed=allowed_release_levels,
        )
        if not allowed_levels:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.releasePolicies[{policy_id}].allowedReleaseLevels 不能为空'
            )
    return release_policy_index


def _validate_lifecycle_state_rows(payload: dict[str, Any], *, schema_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lifecycle_states = _require_list(payload.get('lifecycleStates'), label='dispatch target 注册表.lifecycleStates')
    lifecycle_index = _index_by_id(lifecycle_states, label='dispatch target 注册表.lifecycleStates')
    for lifecycle_id, row in lifecycle_index.items():
        _require_keys(
            row,
            list(schema_payload.get('requiredLifecycleFields') or []),
            label=f'dispatch target 注册表.lifecycleStates[{lifecycle_id}]',
        )
        _require_non_empty_text(row.get('title'), label=f'dispatch target 注册表.lifecycleStates[{lifecycle_id}].title')
        _require_bool(row.get('enableAllowed'), label=f'dispatch target 注册表.lifecycleStates[{lifecycle_id}].enableAllowed')
        _require_bool(row.get('decommissioned'), label=f'dispatch target 注册表.lifecycleStates[{lifecycle_id}].decommissioned')
    return lifecycle_index


def _validate_verification_batches(
    payload: dict[str, Any],
    *,
    schema_payload: dict[str, Any],
    allowed_target_groups: set[str],
) -> dict[str, dict[str, Any]]:
    verification_batches_root = _require_object(
        payload.get('verificationBatches'),
        label='dispatch target 注册表.verificationBatches',
    )
    _require_keys(
        verification_batches_root,
        ['defaultRotationBatchId', 'batches'],
        label='dispatch target 注册表.verificationBatches',
    )
    verification_batches = _require_list(
        verification_batches_root.get('batches'),
        label='dispatch target 注册表.verificationBatches.batches',
    )
    verification_batch_index = _index_by_id(
        verification_batches,
        label='dispatch target 注册表.verificationBatches.batches',
    )
    default_rotation_batch_id = _require_non_empty_text(
        verification_batches_root.get('defaultRotationBatchId'),
        label='dispatch target 注册表.verificationBatches.defaultRotationBatchId',
    )
    if default_rotation_batch_id not in verification_batch_index:
        raise DispatchRegistryValidationError(
            'dispatch target 注册表.verificationBatches.defaultRotationBatchId 未命中已注册批次：'
            f'{default_rotation_batch_id}'
        )
    for batch_id, row in verification_batch_index.items():
        _require_keys(
            row,
            list(schema_payload.get('requiredVerificationBatchFields') or []),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}]',
        )
        _require_non_empty_text(
            row.get('title'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].title',
        )
        _require_non_empty_text(
            row.get('description'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].description',
        )
        _require_bool(
            row.get('requiredForRelease'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].requiredForRelease',
        )
        _require_unique_text_list(
            row.get('requiredTargetGroups'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].requiredTargetGroups',
            allowed=allowed_target_groups,
        )
        _require_unique_text_list(
            row.get('targetIds'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].targetIds',
        )
    return verification_batch_index


def _validate_target_boundary(
    row: dict[str, Any],
    *,
    target_id: str,
    target_group: str,
    delivery_tier: str,
    message_profile: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """校验 target 的职责边界，避免业务、监控和联调目标混用同一运行语义。"""
    boundary = _require_object(row.get('boundary'), label=f'dispatch target 注册表.targets[{target_id}].boundary')
    _require_keys(
        boundary,
        list(contract['schema_payload'].get('requiredBoundaryFields') or []),
        label=f'dispatch target 注册表.targets[{target_id}].boundary',
    )
    dispatch_lane = _require_non_empty_text(
        boundary.get('dispatchLane'),
        label=f'dispatch target 注册表.targets[{target_id}].boundary.dispatchLane',
    )
    if dispatch_lane not in contract['allowed_dispatch_lanes']:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].boundary.dispatchLane 取值非法：{dispatch_lane}'
        )
    payload_scope = _require_non_empty_text(
        boundary.get('payloadScope'),
        label=f'dispatch target 注册表.targets[{target_id}].boundary.payloadScope',
    )
    if payload_scope not in contract['allowed_payload_scopes']:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].boundary.payloadScope 取值非法：{payload_scope}'
        )
    publish_latest = _require_bool(
        boundary.get('publishLatestDefault'),
        label=f'dispatch target 注册表.targets[{target_id}].boundary.publishLatestDefault',
    )
    _require_non_empty_text(
        boundary.get('description'),
        label=f'dispatch target 注册表.targets[{target_id}].boundary.description',
    )
    rules = contract['target_group_boundary_rules']
    rule = _require_object(rules.get(target_group), label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}')
    expected_lane = _require_non_empty_text(
        rule.get('dispatchLane'),
        label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}.dispatchLane',
    )
    if dispatch_lane != expected_lane:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].boundary.dispatchLane 必须与 targetGroup={target_group} 一致；'
            f'当前={dispatch_lane}，期望={expected_lane}'
        )
    allowed_tiers = set(_require_unique_text_list(
        rule.get('allowedDeliveryTiers'),
        label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}.allowedDeliveryTiers',
        allowed=contract['allowed_delivery_tiers'],
    ))
    if delivery_tier not in allowed_tiers:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].deliveryTier={delivery_tier} 不符合 targetGroup={target_group} 边界'
        )
    allowed_profiles = set(_require_unique_text_list(
        rule.get('allowedMessageProfiles'),
        label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}.allowedMessageProfiles',
        allowed=contract['allowed_message_profiles'],
    ))
    if message_profile not in allowed_profiles:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].messageProfile={message_profile} 不符合 targetGroup={target_group} 边界'
        )
    allowed_scopes = set(_require_unique_text_list(
        rule.get('allowedPayloadScopes'),
        label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}.allowedPayloadScopes',
        allowed=contract['allowed_payload_scopes'],
    ))
    if payload_scope not in allowed_scopes:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].boundary.payloadScope={payload_scope} 不符合 targetGroup={target_group} 边界'
        )
    expected_publish_latest = _require_bool(
        rule.get('publishLatestDefault'),
        label=f'dispatch target schema.targetGroupBoundaryRules.{target_group}.publishLatestDefault',
    )
    if publish_latest != expected_publish_latest:
        raise DispatchRegistryValidationError(
            f'dispatch target 注册表.targets[{target_id}].boundary.publishLatestDefault 必须为 '
            f'{str(expected_publish_latest).lower()}'
        )
    return boundary


def _validate_target_rows(
    payload: dict[str, Any],
    *,
    contract: dict[str, Any],
    release_policy_index: dict[str, dict[str, Any]],
    lifecycle_index: dict[str, dict[str, Any]],
    verification_batch_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    targets = _require_list(payload.get('targets'), label='dispatch target 注册表.targets')
    target_index = _index_by_id(targets, label='dispatch target 注册表.targets')
    managed_env_keys: dict[str, str] = {}
    verification_orders: set[int] = set()
    for target_id, row in target_index.items():
        _require_keys(
            row,
            list(contract['schema_payload'].get('requiredTargetFields') or []),
            label=f'dispatch target 注册表.targets[{target_id}]',
        )
        target_transport = _require_non_empty_text(
            row.get('transport'),
            label=f'dispatch target 注册表.targets[{target_id}].transport',
        )
        if target_transport not in contract['allowed_target_transports']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].transport 取值非法：{target_transport}'
            )
        target_provider = _require_non_empty_text(
            row.get('provider'),
            label=f'dispatch target 注册表.targets[{target_id}].provider',
        )
        if target_provider not in contract['allowed_target_providers']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].provider 未在 dispatchProviderRegistryPaths 注册：{target_provider}'
            )
        if (target_provider, target_transport) not in contract['provider_specs']:
            raise DispatchRegistryValidationError(
                'dispatch target 注册表.targets['
                f'{target_id}] 的 provider/transport 组合未在 dispatchProviderRegistryPaths 注册：'
                f'{target_provider}/{target_transport}'
            )
        target_group = _require_non_empty_text(
            row.get('targetGroup'),
            label=f'dispatch target 注册表.targets[{target_id}].targetGroup',
        )
        if target_group not in contract['allowed_target_groups']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].targetGroup 取值非法：{target_group}'
            )
        delivery_tier = _require_non_empty_text(row.get('deliveryTier'), label=f'dispatch target 注册表.targets[{target_id}].deliveryTier')
        if delivery_tier not in contract['allowed_delivery_tiers']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].deliveryTier 取值非法：{delivery_tier}'
            )
        message_profile = _require_non_empty_text(row.get('messageProfile'), label=f'dispatch target 注册表.targets[{target_id}].messageProfile')
        if message_profile not in contract['allowed_message_profiles']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].messageProfile 取值非法：{message_profile}'
            )
        _validate_target_boundary(
            row,
            target_id=target_id,
            target_group=target_group,
            delivery_tier=delivery_tier,
            message_profile=message_profile,
            contract=contract,
        )
        _require_bool(row.get('enabledDefault'), label=f'dispatch target 注册表.targets[{target_id}].enabledDefault')
        _require_bool(
            row.get('silenceEnabledDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].silenceEnabledDefault',
        )
        silence_delta = row.get('silenceMinDeltaDefault')
        if not isinstance(silence_delta, (int, float)) or isinstance(silence_delta, bool):
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].silenceMinDeltaDefault 必须为数字'
            )
        if float(silence_delta) < 0 or float(silence_delta) > 1:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].silenceMinDeltaDefault 必须位于 [0,1]'
            )
        _require_bool(
            row.get('secretRequiredDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].secretRequiredDefault',
        )
        _require_bool(
            row.get('endpointIsolationDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].endpointIsolationDefault',
        )
        _require_bool(row.get('atAllDefault'), label=f'dispatch target 注册表.targets[{target_id}].atAllDefault')
        format_default = _require_non_empty_text(
            row.get('formatDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].formatDefault',
        )
        if format_default not in contract['allowed_formats']:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].formatDefault 取值非法：{format_default}'
            )
        verification_order = _require_int(
            row.get('verificationOrderDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].verificationOrderDefault',
            minimum=1,
        )
        if verification_order in verification_orders:
            raise DispatchRegistryValidationError(
                'dispatch target 注册表.targets[*].verificationOrderDefault 不允许重复：'
                f'{verification_order}'
            )
        verification_orders.add(verification_order)
        owner = _require_object(row.get('owner'), label=f'dispatch target 注册表.targets[{target_id}].owner')
        _require_keys(
            owner,
            list(contract['schema_payload'].get('requiredOwnerFields') or []),
            label=f'dispatch target 注册表.targets[{target_id}].owner',
        )
        for owner_key in contract['schema_payload'].get('requiredOwnerFields') or []:
            _require_non_empty_text(owner.get(owner_key), label=f'dispatch target 注册表.targets[{target_id}].owner.{owner_key}')
        policy_id = _require_non_empty_text(
            row.get('releasePolicyId'),
            label=f'dispatch target 注册表.targets[{target_id}].releasePolicyId',
        )
        if policy_id not in release_policy_index:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].releasePolicyId 未命中已注册策略：{policy_id}'
            )
        lifecycle_state = _require_non_empty_text(
            row.get('lifecycleState'),
            label=f'dispatch target 注册表.targets[{target_id}].lifecycleState',
        )
        if lifecycle_state not in lifecycle_index:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].lifecycleState 未命中已注册生命周期：{lifecycle_state}'
            )
        lifecycle_info = lifecycle_index[lifecycle_state]
        verification_batch_ids = _require_unique_text_list(
            row.get('verificationBatchIds'),
            label=f'dispatch target 注册表.targets[{target_id}].verificationBatchIds',
            allow_empty=bool(lifecycle_info.get('decommissioned')),
        )
        for batch_id in verification_batch_ids:
            if batch_id not in verification_batch_index:
                raise DispatchRegistryValidationError(
                    f'dispatch target 注册表.targets[{target_id}].verificationBatchIds 包含未知批次：{batch_id}'
                )
        _require_non_empty_text(row.get('rotationClass'), label=f'dispatch target 注册表.targets[{target_id}].rotationClass')
        allowed_levels_default = _require_unique_text_list(
            row.get('allowedReleaseLevelsDefault'),
            label=f'dispatch target 注册表.targets[{target_id}].allowedReleaseLevelsDefault',
            allowed=contract['allowed_release_levels'],
        )
        policy_allowed_levels = list(release_policy_index[policy_id].get('allowedReleaseLevels') or [])
        if allowed_levels_default != policy_allowed_levels:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}].allowedReleaseLevelsDefault 必须与 '
                f'releasePolicies[{policy_id}] 一致；当前={allowed_levels_default}，策略={policy_allowed_levels}'
            )
        if bool(row.get('enabledDefault')) and not bool(lifecycle_info.get('enableAllowed')):
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}] 当前 lifecycleState={lifecycle_state}，不得 enabledDefault=true'
            )
        if bool(lifecycle_info.get('decommissioned')) and verification_batch_ids:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.targets[{target_id}] 已下线，不得继续声明 verificationBatchIds'
            )
        unique_env_keys = [
            _require_non_empty_text(row.get(field), label=f'dispatch target 注册表.targets[{target_id}].{field}')
            for field in TARGET_MANAGED_ENV_FIELDS
        ]
        for env_key in unique_env_keys:
            owner_target = managed_env_keys.get(env_key)
            if owner_target and owner_target != target_id:
                raise DispatchRegistryValidationError(
                    f'dispatch target 注册表中的环境键冲突：{env_key} 同时归属 {owner_target} 与 {target_id}'
                )
            managed_env_keys[env_key] = target_id
    return target_index


def _validate_verification_batch_targets(
    verification_batch_index: dict[str, dict[str, Any]],
    *,
    target_index: dict[str, dict[str, Any]],
    lifecycle_index: dict[str, dict[str, Any]],
    allowed_target_groups: set[str],
) -> None:
    for batch_id, row in verification_batch_index.items():
        batch_target_ids = _require_unique_text_list(
            row.get('targetIds'),
            label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].targetIds',
        )
        batch_orders: list[int] = []
        publish_latest_targets: list[str] = []
        for target_id in batch_target_ids:
            if target_id not in target_index:
                raise DispatchRegistryValidationError(
                    f'dispatch target 注册表.verificationBatches.batches[{batch_id}] 包含未注册 target：{target_id}'
                )
            target_row = target_index[target_id]
            if batch_id not in list(target_row.get('verificationBatchIds') or []):
                raise DispatchRegistryValidationError(
                    f'dispatch target 注册表.verificationBatches.batches[{batch_id}] 与 '
                    f'target={target_id}.verificationBatchIds 不一致'
                )
            lifecycle_state = str(target_row.get('lifecycleState') or '')
            if bool((lifecycle_index.get(lifecycle_state) or {}).get('decommissioned')):
                raise DispatchRegistryValidationError(
                    f'dispatch target 注册表.verificationBatches.batches[{batch_id}] 不得包含已下线 target：{target_id}'
                )
            batch_orders.append(_require_int(
                target_row.get('verificationOrderDefault'),
                label=f'dispatch target 注册表.targets[{target_id}].verificationOrderDefault',
                minimum=1,
            ))
            if bool((_require_object(
                target_row.get('boundary'),
                label=f'dispatch target 注册表.targets[{target_id}].boundary',
            )).get('publishLatestDefault')):
                publish_latest_targets.append(target_id)
        if batch_orders != sorted(batch_orders):
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.verificationBatches.batches[{batch_id}].targetIds 必须按 verificationOrderDefault 升序排列'
            )
        declared_groups = sorted({str((target_index[target_id]).get('targetGroup') or '') for target_id in batch_target_ids})
        required_groups = sorted(
            _require_unique_text_list(
                row.get('requiredTargetGroups'),
                label=f'dispatch target 注册表.verificationBatches.batches[{batch_id}].requiredTargetGroups',
                allowed=allowed_target_groups,
            )
        )
        missing_groups = [group for group in required_groups if group not in declared_groups]
        if missing_groups:
            raise DispatchRegistryValidationError(
                'dispatch target 注册表.verificationBatches.batches['
                f'{batch_id}] 缺少 requiredTargetGroups 对应 target：{", ".join(missing_groups)}'
            )
        if bool(row.get('requiredForRelease')) and not publish_latest_targets:
            raise DispatchRegistryValidationError(
                f'dispatch target 注册表.verificationBatches.batches[{batch_id}] 是生产必需批次，必须包含可推进 dispatch latest 的正式目标'
            )


def validate_dispatch_registry_payload(
    payload: dict[str, Any],
    schema: dict[str, Any] | None = None,
    provider_registry_path: Path | Sequence[Path] | None = None,
) -> dict[str, Any]:
    schema_payload = load_dispatch_registry_schema() if schema is None else schema
    _require_keys(payload, list(schema_payload.get('topLevelRequired') or []), label='dispatch target 注册表')
    _validate_dispatch_registry_version(payload, schema_payload=schema_payload)
    _validate_dispatch_defaults(payload, schema_payload=schema_payload)
    contract = _build_dispatch_validation_contract(schema_payload, provider_registry_path=provider_registry_path)
    release_policy_index = _validate_release_policy_rows(
        payload,
        schema_payload=schema_payload,
        allowed_release_levels=contract['allowed_release_levels'],
    )
    lifecycle_index = _validate_lifecycle_state_rows(payload, schema_payload=schema_payload)
    verification_batch_index = _validate_verification_batches(
        payload,
        schema_payload=schema_payload,
        allowed_target_groups=contract['allowed_target_groups'],
    )
    target_index = _validate_target_rows(
        payload,
        contract=contract,
        release_policy_index=release_policy_index,
        lifecycle_index=lifecycle_index,
        verification_batch_index=verification_batch_index,
    )
    _validate_verification_batch_targets(
        verification_batch_index,
        target_index=target_index,
        lifecycle_index=lifecycle_index,
        allowed_target_groups=contract['allowed_target_groups'],
    )
    return payload
