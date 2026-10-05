#!/usr/bin/env python3
"""外部投递 job 的运行结果契约、原子写入与严格验收。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from openclaw.control_plane.run_ledger import resolve_artifact_root
from openclaw.lib.io.json_access import json_array, json_object
from openclaw.lib.io.state import write_json_atomic


OUTCOME_PATH_ENV = 'OPENCLAW_CONTROL_PLANE_OUTCOME_PATH'
JOB_ID_ENV = 'OPENCLAW_CONTROL_PLANE_JOB_ID'
SCHEDULER_RUN_ID_ENV = 'OPENCLAW_CONTROL_PLANE_SCHEDULER_RUN_ID'
BUSINESS_RUN_ID_ENV = 'OPENCLAW_CONTROL_PLANE_BUSINESS_RUN_ID'
OPERATOR_ID_ENV = 'OPENCLAW_CONTROL_PLANE_OPERATOR_ID'
OPERATOR_REASON_ENV = 'OPENCLAW_CONTROL_PLANE_OPERATOR_REASON'
RECOVERY_OF_RUN_ID_ENV = 'OPENCLAW_CONTROL_PLANE_RECOVERY_OF_RUN_ID'

OUTCOME_SCHEMA_VERSION = 1
OUTCOME_STATUSES = frozenset({
    'sent',
    'noop',
    'retry_pending',
    'rate_limited',
    'failed',
    'blocked',
    'dry_run',
})
OUTCOME_OPERATIONS = frozenset({'send', 'retry', 'operator_verify'})
COMPLETION_ROLES = frozenset({'required', 'advisory'})
SUCCESS_STATUSES = frozenset({'sent', 'noop'})
RETRYABLE_STATUSES = frozenset({'retry_pending', 'rate_limited'})
TERMINAL_STATUSES = frozenset({'failed', 'blocked'})
SCHEDULER_STATUS_BY_OUTCOME = {
    'sent': 'succeeded',
    'noop': 'succeeded',
    'retry_pending': 'retry_pending',
    'rate_limited': 'retry_pending',
    'failed': 'failed',
    'blocked': 'blocked',
    'dry_run': 'failed',
}
_ALLOWED_PROOF_TYPES = frozenset({
    'provider_ack',
    'operator_attestation',
    'prior_sent',
    'attempt_record',
    'dry_run_record',
    'none',
})
_REQUIRED_TOP_LEVEL_FIELDS = frozenset({
    'schemaVersion',
    'jobId',
    'schedulerRunId',
    'businessRunId',
    'operation',
    'status',
    'failureClass',
    'statusSignals',
    'evidence',
    'recoveries',
    'details',
})
_REQUIRED_TARGET_FIELDS = frozenset({
    'targetId',
    'completionRole',
    'status',
    'reasonCode',
    'proofType',
    'evidenceRefs',
})
_REQUIRED_EVIDENCE_FIELDS = frozenset({
    'artifactId',
    'relativePath',
    'sha256',
    'generatedAt',
})
_REQUIRED_RECOVERY_FIELDS = frozenset({
    'ofJobId',
    'ofSchedulerRunId',
    'businessRunId',
})
_ALLOWED_TARGET_FIELDS = _REQUIRED_TARGET_FIELDS | frozenset({'providerAck', 'priorSentProof', 'operatorAttestation'})
_ALLOWED_PROVIDER_ACK_FIELDS = frozenset({'httpStatus', 'businessCode'})
_REQUIRED_PRIOR_SENT_FIELDS = frozenset({
    'schedulerRunId',
    'businessRunId',
    'targetId',
    'contentSha256',
    'artifactPath',
})
_REQUIRED_OPERATOR_ATTESTATION_FIELDS = frozenset({
    'attemptSchedulerRunId',
    'contentSha256',
    'artifactPath',
})


def _runtime_job_key(job: Mapping[str, Any]) -> str:
    """解析调度器对当前 job 使用的稳定运行键。

    参数：
        job（Mapping[str, Any]）：已物化的控制平面 job。

    返回：
        str：优先采用 resolvedRuntimeJobKey 的非空运行键。
    """
    return str(job.get('resolvedRuntimeJobKey') or job.get('qualifiedId') or job.get('id') or '').strip()


def _parse_iso(value: object) -> datetime | None:
    """把外部 ISO 8601 值解析为带时区的时间。

    参数：
        value（object）：清单或证据中的时间原始值。

    返回：
        datetime | None：解析后的时间；空值或非法格式返回 ``None``，无时区值按 UTC 处理。

    副作用：
        仅在内存中规范化时间，不修改输入或外部状态。
    """
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _as_int(value: object) -> int | None:
    """把外部契约值安全转换为整数。

    参数：
        value（object）：HTTP 状态或业务码的原始值，布尔值不视为整数。

    返回：
        int | None：可转换的整数；类型或文本非法时返回 ``None``。
    """
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sha256_file(path: Path) -> str:
    """流式计算证据文件 SHA-256。

    参数：
        path（Path）：需要校验内容的文件路径。

    返回：
        str：文件内容的小写十六进制 SHA-256。

    副作用：
        以只读方式打开并遍历文件内容，不写入文件。
    """
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _strict_relative_path(value: object) -> PurePosixPath | None:
    """校验清单路径为无反斜线、无穿越段的 POSIX 相对路径。

    参数：
        value（object）：证据清单提供的路径原始值。

    返回：
        PurePosixPath | None：合法的相对路径；绝对、空白或含穿越段时返回 ``None``。
    """
    text = str(value or '').strip()
    if not text or '\\' in text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        return None
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    """判断解析后的路径是否位于指定受控根目录内。

    参数：
        path（Path）：待检查的候选路径。
        root（Path）：允许访问的受控根目录。

    返回：
        bool：解析成功且候选位于根目录内时为 ``True``。
    """
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def is_delivery_job(job: Mapping[str, Any]) -> bool:
    """判断 job 是否声明了必须由调度器消费的投递结果契约。

    参数：
        job（Mapping[str, Any]）：已物化的控制平面 job。

    返回：
        bool：存在非空 ``resolvedDeliveryContract`` 时为 ``True``。
    """
    return bool(json_object(job.get('resolvedDeliveryContract')))


def build_outcome_manifest(
    *,
    business_run_id: str,
    operation: str,
    status: str,
    failure_class: str | None,
    status_signals: list[str],
    evidence: list[dict[str, Any]],
    recoveries: list[dict[str, Any]],
    target_outcomes: list[dict[str, Any]],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """根据调度器注入的身份构造统一投递结果清单。

    参数：
        business_run_id（str）：业务运行标识，用于关联本次投递产物与逐目标结果。
        operation（str）：本次动作，取值为 send、retry 或 operator_verify。
        status（str）：run 级业务状态。
        failure_class（str | None）：失败分类；成功状态必须为 ``None``。
        status_signals（list[str]）：本次产生的受控状态信号。
        evidence（list[dict[str, Any]]）：不可变产物证据清单。
        recoveries（list[dict[str, Any]]）：本次显式关闭的来源运行引用。
        target_outcomes（list[dict[str, Any]]）：逐目标投递结果。
        env（Mapping[str, str] | None）：环境变量映射；省略时读取当前进程环境。

    返回：
        dict[str, Any]：符合 schema v1 的 outcome manifest。

    副作用：
        ``env`` 缺省时只读取当前进程环境中的调度身份，不写入环境或文件。
    """
    env_map = os.environ if env is None else env
    return {
        'schemaVersion': OUTCOME_SCHEMA_VERSION,
        'jobId': str(env_map.get(JOB_ID_ENV) or '').strip(),
        'schedulerRunId': str(env_map.get(SCHEDULER_RUN_ID_ENV) or '').strip(),
        'businessRunId': str(business_run_id or '').strip(),
        'operation': str(operation or '').strip(),
        'status': str(status or '').strip(),
        'failureClass': str(failure_class or '').strip() or None,
        'statusSignals': [str(item).strip() for item in status_signals if str(item).strip()],
        'evidence': [dict(item) for item in evidence],
        'recoveries': [dict(item) for item in recoveries],
        'details': {'targetOutcomes': [dict(item) for item in target_outcomes]},
    }


def write_outcome_manifest(
    payload: Mapping[str, Any],
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """通过公共原子写入器持久化投递结果清单。

    参数：
        payload（Mapping[str, Any]）：待写入的 outcome manifest。
        path（Path | None）：显式目标路径；省略时使用调度器注入的环境变量。
        env（Mapping[str, str] | None）：环境变量映射；省略时读取当前进程环境。

    返回：
        Path | None：实际写入路径；非调度调用未注入路径时返回 ``None``。

    副作用：
        对目标 JSON 文件执行原子替换写入。
    """
    env_map = os.environ if env is None else env
    target = path
    if target is None:
        raw_path = str(env_map.get(OUTCOME_PATH_ENV) or '').strip()
        if not raw_path:
            return None
        target = Path(raw_path)
    write_json_atomic(target, dict(payload))
    return target


def evidence_record(
    *,
    artifact_id: str,
    root: Path,
    path: Path,
    generated_at: str,
) -> dict[str, Any]:
    """为已落盘文件生成不泄露绝对路径的 SHA-256 证据记录。

    参数：
        artifact_id（str）：控制平面 runtime path 标识。
        root（Path）：证据文件所属的受控根目录。
        path（Path）：已存在的证据文件。
        generated_at（str）：证据产生时间（ISO 8601）。

    返回：
        dict[str, Any]：可写入 outcome manifest 的证据对象。

    异常：
        ValueError：证据文件不位于受控根目录中。
    """
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError(f'证据路径越界：{resolved_path}')
    return {
        'artifactId': str(artifact_id or '').strip(),
        'relativePath': resolved_path.relative_to(resolved_root).as_posix(),
        'sha256': _sha256_file(resolved_path),
        'generatedAt': str(generated_at or '').strip(),
    }


def _validate_evidence(
    evidence: Any,
    *,
    job: Mapping[str, Any],
    started_at: datetime,
    env: Mapping[str, str] | None,
    prior_sent_paths: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """逐条验证 manifest 证据的边界、时间、新鲜度和内容哈希。

    参数：
        evidence（Any）：manifest 中的证据数组原始值。
        job（Mapping[str, Any]）：声明唯一 artifact root 的已物化 job。
        started_at（datetime）：当前调度运行开始时间，用于拒绝陈旧证据。
        env（Mapping[str, str] | None）：artifact root 解析使用的运行环境。
        prior_sent_paths（set[str]）：允许引用早于当前运行的既有 sent 证明相对路径。

    返回：
        tuple[list[dict[str, Any]], list[str]]：通过校验的规范化证据与 fail-closed 原因列表。
    """
    rows = json_array(evidence)
    reasons: list[str] = []
    validated: list[dict[str, Any]] = []
    artifact_policy = json_object(job.get('artifactPolicy'))
    default_artifact_id = str(artifact_policy.get('runArtifactRoot') or '').strip()
    threshold = started_at - timedelta(seconds=5)
    future_threshold = datetime.now(timezone.utc) + timedelta(seconds=5)
    seen_paths: set[str] = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            reasons.append(f'evidence[{index}]_not_object')
            continue
        missing = sorted(_REQUIRED_EVIDENCE_FIELDS - set(raw_row))
        if missing:
            reasons.append(f'evidence[{index}]_missing:{",".join(missing)}')
            continue
        unknown = sorted(set(raw_row) - _REQUIRED_EVIDENCE_FIELDS)
        if unknown:
            reasons.append(f'evidence[{index}]_unknown:{",".join(unknown)}')
            continue
        artifact_id = str(raw_row.get('artifactId') or default_artifact_id).strip()
        relative = _strict_relative_path(raw_row.get('relativePath'))
        declared_hash = str(raw_row.get('sha256') or '').strip().lower()
        generated_at = _parse_iso(raw_row.get('generatedAt'))
        if not artifact_id:
            reasons.append(f'evidence[{index}]_artifact_id_empty')
            continue
        if not default_artifact_id or artifact_id != default_artifact_id:
            reasons.append(f'evidence[{index}]_artifact_id_not_declared:{artifact_id}')
            continue
        if relative is None:
            reasons.append(f'evidence[{index}]_relative_path_invalid')
            continue
        relative_text = relative.as_posix()
        prior_sent_reference = relative_text in prior_sent_paths
        if relative_text in seen_paths:
            reasons.append(f'evidence[{index}]_duplicate_relative_path')
            continue
        seen_paths.add(relative_text)
        if len(declared_hash) != 64 or any(ch not in '0123456789abcdef' for ch in declared_hash):
            reasons.append(f'evidence[{index}]_sha256_invalid')
            continue
        if generated_at is None:
            reasons.append(f'evidence[{index}]_generated_at_invalid')
            continue
        if not prior_sent_reference and generated_at.astimezone(timezone.utc) < threshold.astimezone(timezone.utc):
            reasons.append(f'evidence[{index}]_generated_at_stale')
            continue
        if generated_at.astimezone(timezone.utc) > future_threshold:
            reasons.append(f'evidence[{index}]_generated_at_future')
            continue
        root = resolve_artifact_root(artifact_id, env=dict(env or {}))
        if root is None:
            reasons.append(f'evidence[{index}]_artifact_root_unknown:{artifact_id}')
            continue
        root = root.resolve()
        candidate = (root / Path(*relative.parts)).resolve()
        if not _is_relative_to(candidate, root):
            reasons.append(f'evidence[{index}]_path_escape')
            continue
        try:
            stat = candidate.stat()
        except OSError:
            reasons.append(f'evidence[{index}]_file_missing')
            continue
        if not candidate.is_file():
            reasons.append(f'evidence[{index}]_not_file')
            continue
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if not prior_sent_reference and modified_at < threshold.astimezone(timezone.utc):
            reasons.append(f'evidence[{index}]_file_stale')
            continue
        if modified_at > future_threshold:
            reasons.append(f'evidence[{index}]_file_mtime_future')
            continue
        if abs((generated_at.astimezone(timezone.utc) - modified_at).total_seconds()) > 5:
            reasons.append(f'evidence[{index}]_generated_at_mtime_mismatch')
            continue
        try:
            actual_hash = _sha256_file(candidate)
        except OSError:
            reasons.append(f'evidence[{index}]_hash_read_failed')
            continue
        if actual_hash != declared_hash:
            reasons.append(f'evidence[{index}]_sha256_mismatch')
            continue
        validated.append({
            'artifactId': artifact_id,
            'relativePath': relative_text,
            'sha256': actual_hash,
            'generatedAt': str(raw_row.get('generatedAt') or ''),
            'path': str(candidate),
        })
    return validated, reasons


def _aggregate_required_status(target_outcomes: list[dict[str, Any]], *, operation: str) -> str | None:
    """按固定优先级聚合 required 目标的 run 级状态。

    参数：
        target_outcomes（list[dict[str, Any]]）：已规范化的逐目标结果。
        operation（str）：当前 ``send``、``retry`` 或 ``operator_verify`` 动作。

    返回：
        str | None：合法聚合状态；目标集合或状态组合不满足契约时返回 ``None``。
    """
    required = [item for item in target_outcomes if item.get('completionRole') == 'required']
    if not required:
        if operation == 'retry' and not target_outcomes:
            return 'noop'
        if target_outcomes and all(item.get('status') == 'dry_run' for item in target_outcomes):
            return 'dry_run'
        return None
    statuses = {str(item.get('status') or '') for item in required}
    for status in ('failed', 'blocked', 'rate_limited', 'retry_pending'):
        if status in statuses:
            return status
    if statuses.issubset(SUCCESS_STATUSES):
        return 'sent' if 'sent' in statuses else 'noop'
    if statuses == {'dry_run'}:
        return 'dry_run'
    return None


def _evidence_json(evidence_by_path: dict[str, dict[str, Any]], relative_path: str) -> dict[str, Any]:
    """读取已通过边界校验的 JSON 证据文件。

    参数：
        evidence_by_path（dict[str, dict[str, Any]]）：按相对路径索引的已验证证据元数据。
        relative_path（str）：目标证据相对路径。

    返回：
        dict[str, Any]：顶层 JSON 对象；路径未登记、读取失败或格式非法时返回空字典。
    """
    row = evidence_by_path.get(relative_path) or {}
    path = Path(str(row.get('path') or ''))
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _provider_sent_evidence_valid(
    evidence_by_path: dict[str, dict[str, Any]],
    relative_paths: list[str],
    *,
    business_run_id: str,
    target_id: str,
    scheduler_run_id: str,
    content_sha256: str = '',
) -> bool:
    """核验 provider sent 证据绑定到同一运行、目标与内容。

    参数：
        evidence_by_path（dict[str, dict[str, Any]]）：已验证证据的相对路径索引。
        relative_paths（list[str]）：目标结果引用的证据相对路径。
        business_run_id（str）：期望业务运行标识。
        target_id（str）：期望目标标识。
        scheduler_run_id（str）：期望 provider attempt 的调度运行标识。
        content_sha256（str）：可选的期望消息正文 SHA-256；空值时不额外约束。

    返回：
        bool：存在严格回执且全部身份字段一致的证据时为 ``True``。
    """
    for relative_path in relative_paths:
        payload = _evidence_json(evidence_by_path, relative_path)
        result = json_object(payload.get('result'))
        ack = json_object(result.get('providerAck'))
        source = json_object(payload.get('source'))
        evidence_content_sha256 = str(result.get('contentSha256') or source.get('contentSha256') or '').lower()
        http_status = _as_int(ack.get('httpStatus'))
        if (
            str(payload.get('runId') or '') == business_run_id
            and str(payload.get('targetId') or result.get('targetId') or '') == target_id
            and str(payload.get('schedulerRunId') or '') == scheduler_run_id
            and str(result.get('status') or '') == 'sent'
            and str(result.get('proofType') or '') == 'provider_ack'
            and http_status is not None
            and 200 <= http_status < 300
            and ack.get('businessCode') in (0, '0')
            and (not content_sha256 or evidence_content_sha256 == content_sha256.lower())
        ):
            return True
    return False


def _operator_attestation_evidence_valid(
    evidence_by_path: dict[str, dict[str, Any]],
    relative_path: str,
    *,
    business_run_id: str,
    target_id: str,
    content_sha256: str,
    attempt_scheduler_run_id: str = '',
    proof_scheduler_run_id: str = '',
) -> bool:
    """核验人工 attestation 只闭环同一未知送达 attempt。

    参数：
        evidence_by_path（dict[str, dict[str, Any]]）：已验证证据的相对路径索引。
        relative_path（str）：人工证明的相对路径。
        business_run_id（str）：期望业务运行标识。
        target_id（str）：期望目标标识。
        content_sha256（str）：操作员确认可见的正文 SHA-256。
        attempt_scheduler_run_id（str）：可选的原未知送达调度运行标识。
        proof_scheduler_run_id（str）：可选的人工证明调度运行标识。

    返回：
        bool：身份、操作员审计、未知送达来源和永久 watch 信号均有效时为 ``True``。
    """
    payload = _evidence_json(evidence_by_path, relative_path)
    verification = json_object(payload.get('verificationOf'))
    confirmed_at = _parse_iso(payload.get('confirmedAt'))
    status_signals = {
        str(item).strip()
        for item in json_array(payload.get('statusSignals'))
        if str(item).strip()
    }
    return bool(
        payload.get('schemaVersion') == 1
        and payload.get('proofType') == 'operator_attestation'
        and payload.get('confirmedVisible') is True
        and str(payload.get('businessRunId') or '') == business_run_id
        and str(payload.get('targetId') or '') == target_id
        and str(payload.get('contentSha256') or '').lower() == content_sha256.lower()
        and str(payload.get('operatorId') or '').strip()
        and str(payload.get('operatorReason') or '').strip()
        and confirmed_at is not None
        and str(verification.get('schedulerRunId') or '').strip()
        and str(verification.get('artifactPath') or '').strip()
        and str(verification.get('failureClass') or '') == 'delivery_ack_unknown'
        and {'dispatch_manual_verified', 'dispatch_advisory'}.issubset(status_signals)
        and (not attempt_scheduler_run_id or str(verification.get('schedulerRunId') or '') == attempt_scheduler_run_id)
        and (not proof_scheduler_run_id or str(payload.get('schedulerRunId') or '') == proof_scheduler_run_id)
    )


def _prior_sent_evidence_valid(
    evidence_by_path: dict[str, dict[str, Any]],
    *,
    prior: dict[str, Any],
) -> bool:
    """验证 noop 引用的 prior sent 证明仍可独立复核。

    参数：
        evidence_by_path（dict[str, dict[str, Any]]）：已验证证据的相对路径索引。
        prior（dict[str, Any]）：目标结果中的 priorSentProof 身份对象。

    返回：
        bool：引用的 provider 或人工 sent 证明有效时为 ``True``。
    """
    artifact_path = str(prior.get('artifactPath') or '')
    business_run_id = str(prior.get('businessRunId') or '')
    target_id = str(prior.get('targetId') or '')
    if _provider_sent_evidence_valid(
        evidence_by_path,
        [artifact_path],
        business_run_id=business_run_id,
        target_id=target_id,
        scheduler_run_id=str(prior.get('schedulerRunId') or ''),
        content_sha256=str(prior.get('contentSha256') or ''),
    ):
        return True
    return _operator_attestation_evidence_valid(
        evidence_by_path,
        artifact_path,
        business_run_id=business_run_id,
        target_id=target_id,
        content_sha256=str(prior.get('contentSha256') or ''),
        proof_scheduler_run_id=str(prior.get('schedulerRunId') or ''),
    )


def _validate_target_outcomes(
    value: Any,
    *,
    operation: str,
    manifest_status: str,
    evidence_by_path: dict[str, dict[str, Any]],
    business_run_id: str,
    scheduler_run_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """校验逐目标结果字段、证明类型和 required 聚合一致性。

    参数：
        value（Any）：details.targetOutcomes 的原始值。
        operation（str）：当前投递动作。
        manifest_status（str）：清单声明的 run 级状态。
        evidence_by_path（dict[str, dict[str, Any]]）：已验证证据的相对路径索引。
        business_run_id（str）：清单业务运行标识。
        scheduler_run_id（str）：清单调度运行标识。

    返回：
        tuple[list[dict[str, Any]], list[str]]：规范化目标结果与所有契约拒绝原因。
    """
    rows = json_array(value)
    reasons: list[str] = []
    normalized: list[dict[str, Any]] = []
    evidence_paths = set(evidence_by_path)
    seen_target_ids: set[str] = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            reasons.append(f'target_outcomes[{index}]_not_object')
            continue
        missing = sorted(_REQUIRED_TARGET_FIELDS - set(raw_row))
        if missing:
            reasons.append(f'target_outcomes[{index}]_missing:{",".join(missing)}')
            continue
        unknown = sorted(set(raw_row) - _ALLOWED_TARGET_FIELDS)
        if unknown:
            reasons.append(f'target_outcomes[{index}]_unknown:{",".join(unknown)}')
        target_id = str(raw_row.get('targetId') or '').strip()
        role = str(raw_row.get('completionRole') or '').strip()
        status = str(raw_row.get('status') or '').strip()
        reason_code = str(raw_row.get('reasonCode') or '').strip()
        proof_type = str(raw_row.get('proofType') or '').strip()
        evidence_refs = [str(item).strip() for item in json_array(raw_row.get('evidenceRefs')) if str(item).strip()]
        if not target_id or target_id in seen_target_ids:
            reasons.append(f'target_outcomes[{index}]_target_id_invalid')
        else:
            seen_target_ids.add(target_id)
        if role not in COMPLETION_ROLES:
            reasons.append(f'target_outcomes[{index}]_completion_role_invalid')
        if status not in OUTCOME_STATUSES:
            reasons.append(f'target_outcomes[{index}]_status_invalid')
        if not reason_code:
            reasons.append(f'target_outcomes[{index}]_reason_code_empty')
        if proof_type not in _ALLOWED_PROOF_TYPES:
            reasons.append(f'target_outcomes[{index}]_proof_type_invalid')
        missing_refs = sorted(set(evidence_refs) - evidence_paths)
        if missing_refs:
            reasons.append(f'target_outcomes[{index}]_evidence_ref_unknown')
        if len(evidence_refs) != len(set(evidence_refs)):
            reasons.append(f'target_outcomes[{index}]_evidence_ref_duplicate')
        if status in SUCCESS_STATUSES and not evidence_refs:
            reasons.append(f'target_outcomes[{index}]_success_without_evidence')
        if status == 'sent' and proof_type not in {'provider_ack', 'operator_attestation'}:
            reasons.append(f'target_outcomes[{index}]_sent_proof_invalid')
        if status == 'sent' and proof_type == 'provider_ack':
            ack = json_object(raw_row.get('providerAck'))
            http_status = _as_int(ack.get('httpStatus'))
            if not (http_status is not None and 200 <= http_status < 300 and ack.get('businessCode') in (0, '0')):
                reasons.append(f'target_outcomes[{index}]_provider_ack_not_accepted')
            elif not _provider_sent_evidence_valid(
                evidence_by_path,
                evidence_refs,
                business_run_id=business_run_id,
                target_id=target_id,
                scheduler_run_id=scheduler_run_id,
            ):
                reasons.append(f'target_outcomes[{index}]_provider_ack_evidence_invalid')
        if status == 'sent' and proof_type == 'operator_attestation':
            attestation = json_object(raw_row.get('operatorAttestation'))
            content_sha256 = str(attestation.get('contentSha256') or '').lower()
            if (
                set(attestation) != _REQUIRED_OPERATOR_ATTESTATION_FIELDS
                or not str(attestation.get('attemptSchedulerRunId') or '')
                or len(content_sha256) != 64
                or any(ch not in '0123456789abcdef' for ch in content_sha256)
                or str(attestation.get('artifactPath') or '') not in evidence_paths
            ):
                reasons.append(f'target_outcomes[{index}]_operator_attestation_invalid')
            elif not _operator_attestation_evidence_valid(
                evidence_by_path,
                str(attestation.get('artifactPath') or ''),
                business_run_id=business_run_id,
                target_id=target_id,
                content_sha256=content_sha256,
                attempt_scheduler_run_id=str(attestation.get('attemptSchedulerRunId') or ''),
                proof_scheduler_run_id=scheduler_run_id,
            ):
                reasons.append(f'target_outcomes[{index}]_operator_attestation_evidence_invalid')
        if status == 'noop' and proof_type != 'prior_sent':
            reasons.append(f'target_outcomes[{index}]_noop_proof_invalid')
        if status == 'noop':
            prior = json_object(raw_row.get('priorSentProof'))
            if set(prior) != _REQUIRED_PRIOR_SENT_FIELDS:
                reasons.append(f'target_outcomes[{index}]_prior_sent_fields_invalid')
            elif (
                not str(prior.get('schedulerRunId') or '')
                or str(prior.get('businessRunId') or '') != business_run_id
                or str(prior.get('targetId') or '') != target_id
                or len(str(prior.get('contentSha256') or '')) != 64
                or any(ch not in '0123456789abcdef' for ch in str(prior.get('contentSha256') or '').lower())
                or str(prior.get('artifactPath') or '') not in evidence_paths
            ):
                reasons.append(f'target_outcomes[{index}]_prior_sent_identity_invalid')
            elif not _prior_sent_evidence_valid(evidence_by_path, prior=prior):
                reasons.append(f'target_outcomes[{index}]_prior_sent_evidence_invalid')
        provider_ack = raw_row.get('providerAck')
        if provider_ack is not None:
            if not isinstance(provider_ack, dict):
                reasons.append(f'target_outcomes[{index}]_provider_ack_not_object')
            else:
                provider_unknown = sorted(set(provider_ack) - _ALLOWED_PROVIDER_ACK_FIELDS)
                if provider_unknown:
                    reasons.append(f'target_outcomes[{index}]_provider_ack_unknown:{",".join(provider_unknown)}')
                if not isinstance(provider_ack.get('httpStatus', 0), int):
                    reasons.append(f'target_outcomes[{index}]_provider_http_status_invalid')
                if not isinstance(provider_ack.get('businessCode'), (str, int, type(None))):
                    reasons.append(f'target_outcomes[{index}]_provider_business_code_invalid')
        normalized.append({
            **raw_row,
            'targetId': target_id,
            'completionRole': role,
            'status': status,
            'reasonCode': reason_code,
            'proofType': proof_type,
            'evidenceRefs': evidence_refs,
        })
    aggregate = _aggregate_required_status(normalized, operation=operation)
    if aggregate != manifest_status:
        reasons.append(f'aggregate_status_mismatch:expected={aggregate or "invalid"},actual={manifest_status}')
    return normalized, reasons


def _job_ref_matches(actual: str, expected: str) -> bool:
    """比较限定或未限定形式的 job 引用是否指向同一对象。

    参数：
        actual（str）：recovery 行实际声明的来源 job。
        expected（str）：当前配置授权的来源 job。

    返回：
        bool：完整引用或去除 extension 前缀后的引用相交时为 ``True``。
    """
    actual_values = {actual, actual.split(':', 1)[-1]}
    expected_values = {expected, expected.split(':', 1)[-1]}
    return bool(actual_values.intersection(expected_values))


def _validate_recoveries(
    value: Any,
    *,
    business_run_id: str,
    job: Mapping[str, Any],
    operation: str,
    target_outcomes: list[dict[str, Any]],
    env: Mapping[str, str] | None,
) -> tuple[list[dict[str, str]], list[str]]:
    """校验 recovery 只精确关闭配置授权的原运行。

    参数：
        value（Any）：manifest 中 recoveries 数组的原始值。
        business_run_id（str）：当前业务运行标识。
        job（Mapping[str, Any]）：包含 resolvedRecoveryStep 的当前 job。
        operation（str）：当前投递动作。
        target_outcomes（list[dict[str, Any]]）：已经规范化的逐目标结果。
        env（Mapping[str, str] | None）：受控单 job 执行注入的 recovery 来源环境。

    返回：
        tuple[list[dict[str, str]], list[str]]：合法 recovery 引用与 fail-closed 原因列表。
    """
    rows = json_array(value)
    reasons: list[str] = []
    normalized: list[dict[str, str]] = []
    recovery_step = json_object(job.get('resolvedRecoveryStep'))
    configured_origin_job = str(recovery_step.get('recoveryOfJobRef') or '').strip()
    requested_origin_run = str((env or {}).get(RECOVERY_OF_RUN_ID_ENV) or '').strip()
    expected_origin_job = configured_origin_job or (_runtime_job_key(job) if requested_origin_run else '')
    seen: set[tuple[str, str, str]] = set()
    if rows:
        required = [item for item in target_outcomes if item.get('completionRole') == 'required']
        if not required or any(str(item.get('status') or '') not in SUCCESS_STATUSES for item in required):
            reasons.append('recovery_without_accepted_required_targets')
        if operation not in OUTCOME_OPERATIONS:
            reasons.append('recovery_operation_invalid')
        if not expected_origin_job:
            reasons.append('recovery_not_authorized_for_job')
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            reasons.append(f'recoveries[{index}]_not_object')
            continue
        missing = sorted(_REQUIRED_RECOVERY_FIELDS - set(raw_row))
        if missing:
            reasons.append(f'recoveries[{index}]_missing:{",".join(missing)}')
            continue
        unknown = sorted(set(raw_row) - _REQUIRED_RECOVERY_FIELDS)
        if unknown:
            reasons.append(f'recoveries[{index}]_unknown:{",".join(unknown)}')
            continue
        row = {key: str(raw_row.get(key) or '').strip() for key in _REQUIRED_RECOVERY_FIELDS}
        if not all(row.values()):
            reasons.append(f'recoveries[{index}]_empty_identity')
            continue
        if row['businessRunId'] != business_run_id:
            reasons.append(f'recoveries[{index}]_business_run_mismatch')
            continue
        if expected_origin_job and not _job_ref_matches(row['ofJobId'], expected_origin_job):
            reasons.append(f'recoveries[{index}]_origin_job_mismatch')
            continue
        if requested_origin_run and row['ofSchedulerRunId'] != requested_origin_run:
            reasons.append(f'recoveries[{index}]_origin_run_mismatch')
            continue
        identity = (row['ofJobId'], row['ofSchedulerRunId'], row['businessRunId'])
        if identity in seen:
            reasons.append(f'recoveries[{index}]_duplicate')
            continue
        seen.add(identity)
        normalized.append(row)
    return normalized, reasons


def validate_outcome_manifest(
    payload: Any,
    *,
    job: Mapping[str, Any],
    scheduler_run_id: str,
    started_at: str,
    env: Mapping[str, str] | None = None,
    expected_business_run_id: str | None = None,
) -> dict[str, Any]:
    """严格验证投递结果身份、业务聚合和不可变证据。

    参数：
        payload（Any）：从 outcome JSON 解析得到的对象。
        job（Mapping[str, Any]）：当前已物化 job。
        scheduler_run_id（str）：调度器本次运行标识。
        started_at（str）：调度子进程开始时间（ISO 8601）。
        env（Mapping[str, str] | None）：用于解析 artifact root 的运行环境。
        expected_business_run_id（str | None）：受控单 job 执行时要求的业务运行标识。

    返回：
        dict[str, Any]：包含 manifestValid、contractAccepted、artifactAccepted、
        schedulerStatus、failureClass、reasons 与规范化 manifest 的验收结果。
    """
    reasons: list[str] = []
    if not isinstance(payload, dict):
        return {
            'manifestValid': False,
            'contractAccepted': False,
            'artifactAccepted': False,
            'schedulerStatus': 'failed',
            'failureClass': 'target_contract_violation',
            'reasons': ['outcome_not_object'],
            'manifest': None,
            'evidence': [],
        }
    missing = sorted(_REQUIRED_TOP_LEVEL_FIELDS - set(payload))
    if missing:
        reasons.append(f'outcome_missing:{",".join(missing)}')
    unknown = sorted(set(payload) - _REQUIRED_TOP_LEVEL_FIELDS)
    if unknown:
        reasons.append(f'outcome_unknown:{",".join(unknown)}')
    if payload.get('schemaVersion') != OUTCOME_SCHEMA_VERSION:
        reasons.append('schema_version_invalid')
    expected_job_id = _runtime_job_key(job)
    job_id = str(payload.get('jobId') or '').strip()
    actual_scheduler_run_id = str(payload.get('schedulerRunId') or '').strip()
    business_run_id = str(payload.get('businessRunId') or '').strip()
    operation = str(payload.get('operation') or '').strip()
    status = str(payload.get('status') or '').strip()
    failure_class = str(payload.get('failureClass') or '').strip() or None
    if payload.get('failureClass') is not None and not isinstance(payload.get('failureClass'), str):
        reasons.append('failure_class_type_invalid')
    if not expected_job_id or job_id != expected_job_id:
        reasons.append('job_id_mismatch')
    if not scheduler_run_id or actual_scheduler_run_id != scheduler_run_id:
        reasons.append('scheduler_run_id_mismatch')
    if not business_run_id:
        reasons.append('business_run_id_empty')
    if expected_business_run_id and business_run_id != str(expected_business_run_id).strip():
        reasons.append('business_run_id_mismatch')
    if operation not in OUTCOME_OPERATIONS:
        reasons.append('operation_invalid')
    if status not in OUTCOME_STATUSES:
        reasons.append('status_invalid')
    if status in SUCCESS_STATUSES and failure_class is not None:
        reasons.append('success_failure_class_must_be_null')
    if status in RETRYABLE_STATUSES | TERMINAL_STATUSES and failure_class is None:
        reasons.append('failure_class_required')
    started_dt = _parse_iso(started_at)
    if started_dt is None:
        reasons.append('run_started_at_invalid')
        started_dt = datetime.now(timezone.utc)
    if not isinstance(payload.get('evidence'), list):
        reasons.append('evidence_not_array')
    raw_details = json_object(payload.get('details'))
    prior_sent_paths = {
        str(json_object(row.get('priorSentProof')).get('artifactPath') or '')
        for row in json_array(raw_details.get('targetOutcomes'))
        if isinstance(row, dict) and str(row.get('status') or '') == 'noop'
    }
    prior_sent_paths.discard('')
    validated_evidence, evidence_reasons = _validate_evidence(
        payload.get('evidence'),
        job=job,
        started_at=started_dt,
        env=env,
        prior_sent_paths=prior_sent_paths,
    )
    reasons.extend(evidence_reasons)
    evidence_by_path = {
        str(item.get('relativePath') or ''): item
        for item in validated_evidence
        if str(item.get('relativePath') or '')
    }
    details = json_object(payload.get('details'))
    if not isinstance(payload.get('details'), dict):
        reasons.append('details_not_object')
    elif set(details) != {'targetOutcomes'}:
        reasons.append('details_fields_invalid')
    if not isinstance(details.get('targetOutcomes'), list):
        reasons.append('target_outcomes_not_array')
    target_outcomes, target_reasons = _validate_target_outcomes(
        details.get('targetOutcomes'),
        operation=operation,
        manifest_status=status,
        evidence_by_path=evidence_by_path,
        business_run_id=business_run_id,
        scheduler_run_id=actual_scheduler_run_id,
    )
    reasons.extend(target_reasons)
    if not isinstance(payload.get('recoveries'), list):
        reasons.append('recoveries_not_array')
    recoveries, recovery_reasons = _validate_recoveries(
        payload.get('recoveries'),
        business_run_id=business_run_id,
        job=job,
        operation=operation,
        target_outcomes=target_outcomes,
        env=env,
    )
    reasons.extend(recovery_reasons)
    if not isinstance(payload.get('statusSignals'), list):
        reasons.append('status_signals_not_array')
    signals = [str(item).strip() for item in json_array(payload.get('statusSignals')) if str(item).strip()]
    if any(not isinstance(item, str) or not item.strip() for item in json_array(payload.get('statusSignals'))):
        reasons.append('status_signals_item_invalid')
    if len(signals) != len(set(signals)):
        reasons.append('status_signals_duplicate')
    declared_signals = {
        str(item).strip()
        for item in json_array(json_object(job.get('resolvedOutputs')).get('statusSignals'))
        if str(item).strip()
    }
    unknown_signals = sorted(set(signals) - declared_signals)
    if unknown_signals:
        reasons.append(f'status_signals_undeclared:{",".join(unknown_signals)}')
    contract = json_object(job.get('resolvedDeliveryContract'))
    declared_success = {str(item) for item in json_array(contract.get('successStatuses'))}
    declared_retryable = {str(item) for item in json_array(contract.get('retryableStatuses'))}
    declared_terminal = {str(item) for item in json_array(contract.get('terminalStatuses'))}
    if status in SUCCESS_STATUSES and status not in declared_success:
        reasons.append('status_not_in_success_contract')
    if status in RETRYABLE_STATUSES and status not in declared_retryable:
        reasons.append('status_not_in_retryable_contract')
    if status in TERMINAL_STATUSES and status not in declared_terminal:
        reasons.append('status_not_in_terminal_contract')
    failure_policy = json_object(job.get('failureClassPolicy'))
    retryable_classes = {str(item) for item in json_array(failure_policy.get('retryableClasses'))}
    if status in RETRYABLE_STATUSES and failure_class not in retryable_classes:
        reasons.append('retryable_failure_class_undeclared')
    artifact_accepted = not evidence_reasons and (
        bool(validated_evidence)
        or (operation == 'retry' and status == 'noop' and not target_outcomes)
    )
    if status != 'noop' or target_outcomes:
        if not validated_evidence:
            reasons.append('outcome_without_valid_evidence')
            artifact_accepted = False
    manifest_valid = not reasons
    contract_accepted = bool(manifest_valid and status in SUCCESS_STATUSES)
    normalized_manifest = {
        **payload,
        'jobId': job_id,
        'schedulerRunId': actual_scheduler_run_id,
        'businessRunId': business_run_id,
        'operation': operation,
        'status': status,
        'failureClass': failure_class,
        'statusSignals': signals,
        'evidence': validated_evidence,
        'recoveries': recoveries,
        'details': {'targetOutcomes': target_outcomes},
    }
    return {
        'manifestValid': manifest_valid,
        'contractAccepted': contract_accepted,
        'artifactAccepted': bool(manifest_valid and artifact_accepted),
        'schedulerStatus': SCHEDULER_STATUS_BY_OUTCOME.get(status, 'failed') if manifest_valid else 'failed',
        'failureClass': failure_class if manifest_valid else 'target_contract_violation',
        'reasons': reasons,
        'manifest': normalized_manifest,
        'evidence': validated_evidence,
    }


def load_and_validate_outcome_manifest(
    path: Path,
    *,
    job: Mapping[str, Any],
    scheduler_run_id: str,
    started_at: str,
    env: Mapping[str, str] | None = None,
    expected_business_run_id: str | None = None,
) -> dict[str, Any]:
    """读取 outcome JSON 并执行与 ``validate_outcome_manifest`` 相同的严格验收。

    参数：
        path（Path）：调度器为当前运行分配的唯一 outcome 路径。
        job（Mapping[str, Any]）：当前已物化 job。
        scheduler_run_id（str）：调度器本次运行标识。
        started_at（str）：调度子进程开始时间。
        env（Mapping[str, str] | None）：用于解析 artifact root 的运行环境。
        expected_business_run_id（str | None）：受控单 job 执行要求的业务运行标识。

    返回：
        dict[str, Any]：严格验收结果；缺失或损坏文件会 fail closed。
    """
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {
            'manifestValid': False,
            'contractAccepted': False,
            'artifactAccepted': False,
            'schedulerStatus': 'failed',
            'failureClass': 'target_contract_violation',
            'reasons': ['outcome_missing'],
            'manifest': None,
            'evidence': [],
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            'manifestValid': False,
            'contractAccepted': False,
            'artifactAccepted': False,
            'schedulerStatus': 'failed',
            'failureClass': 'target_contract_violation',
            'reasons': [f'outcome_unreadable:{type(exc).__name__}'],
            'manifest': None,
            'evidence': [],
        }
    return validate_outcome_manifest(
        payload,
        job=job,
        scheduler_run_id=scheduler_run_id,
        started_at=started_at,
        env=env,
        expected_business_run_id=expected_business_run_id,
    )
