from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openclaw.control_plane.manifest_fields import (
    DISPATCH_PROVIDER_REGISTRY_PATHS_KEY,
    DISPATCH_TARGET_REGISTRY_PATHS_KEY,
)


def target_passthrough_args(target_id: str, *, push_run_id: str = '', dry_run: bool = False) -> list[str]:
    args = ['--target', target_id]
    if dry_run:
        args.extend(['--dry-run', 'true'])
    normalized_push_run_id = str(push_run_id or '').strip()
    if normalized_push_run_id:
        args.extend(['--push-run-id', normalized_push_run_id])
    return args


def run_target_operation(
    registry: dict[str, Any],
    *,
    target_id: str,
    operation: str,
    extra_args: list[str],
    subprocess_run: Callable[..., Any],
    resolve_dispatch_target_operation_command: Callable[..., list[str]],
) -> dict[str, Any]:
    command = resolve_dispatch_target_operation_command(
        registry,
        dispatch_target_id=target_id,
        operation=operation,
        extra_args=extra_args,
    )
    process = subprocess_run(command, check=False)
    return {
        'operation': operation,
        'command': command,
        'exit_code': int(process.returncode),
        'status': 'pass' if int(process.returncode) == 0 else 'fail',
    }


def verify_target(
    opts: dict[str, Any],
    *,
    config_path_resolver: Callable[[dict[str, Any]], Path | None],
    registry_loader: Callable[[Path | None], dict[str, Any]],
    run_target_operation: Callable[..., dict[str, Any]],
    target_acceptance_payload: Callable[..., dict[str, Any]],
    exit_code_from_status: Callable[..., int],
    fail: Callable[[str, int], Any],
) -> tuple[dict[str, Any], int]:
    target_id = str(opts.get('target') or '').strip()
    if not target_id:
        fail('verify-target requires --target')
    config_path = config_path_resolver(opts)
    registry = registry_loader(config_path)
    push_run_id = str(opts.get('push_run_id') or '').strip()
    operation_rows = [
        run_target_operation(
            registry,
            target_id=target_id,
            operation='preflight',
            extra_args=target_passthrough_args(target_id, push_run_id=push_run_id),
        ),
        run_target_operation(
            registry,
            target_id=target_id,
            operation='send',
            extra_args=target_passthrough_args(target_id, push_run_id=push_run_id, dry_run=True),
        ),
    ]
    if opts.get('real_send'):
        operation_rows.append(
            run_target_operation(
                registry,
                target_id=target_id,
                operation='send',
                extra_args=target_passthrough_args(target_id, push_run_id=push_run_id),
            )
        )
    if not opts.get('skip_explain'):
        operation_rows.append(
            run_target_operation(
                registry,
                target_id=target_id,
                operation='explain_latest',
                extra_args=target_passthrough_args(target_id, push_run_id=push_run_id),
            )
        )
    operation_failures = [row['operation'] for row in operation_rows if str(row.get('status') or '') != 'pass']
    payload = target_acceptance_payload(target_id, config_path=config_path)
    if operation_failures:
        blocking = [str(item).strip() for item in list(payload.get('blocking_issues') or []) if str(item).strip()]
        for item in operation_failures:
            failure_code = f'operation_{item}_failed'
            if failure_code not in blocking:
                blocking.append(failure_code)
        payload['blocking_issues'] = blocking
        payload['status'] = 'fail'
    payload['operations'] = operation_rows
    payload['operation_failures'] = operation_failures
    payload['push_run_id'] = push_run_id or None
    payload['requested_real_send'] = bool(opts.get('real_send'))
    payload['acceptance_summary_skipped'] = bool(opts.get('skip_acceptance_summary'))
    if operation_failures:
        return payload, 2
    return payload, exit_code_from_status(
        str(payload.get('status') or ''),
        fail_on_warn=bool(opts.get('fail_on_warn')),
        fail_on_fail=bool(opts.get('fail_on_fail')),
    )


def collect_targets(
    opts: dict[str, Any],
    *,
    config_path_resolver: Callable[[dict[str, Any]], Path | None],
    registry_loader: Callable[[Path | None], dict[str, Any]],
    env_loader: Callable[[Path], dict[str, str]],
    dispatch_registry_loader: Callable[..., dict[str, Any]],
    targets_payload_loader: Callable[..., tuple[Any, list[Any]]],
    fail: Callable[[str, int], Any],
) -> str:
    gate_env_file = str(opts.get('gate_env_file') or '').strip()
    if not gate_env_file:
        fail('collect-targets requires --gate-env-file')
    env_path = Path(gate_env_file).resolve()
    if not env_path.exists():
        return ''
    config_path = config_path_resolver(opts)
    registry = registry_loader(config_path)
    registry_paths = registry.get('registryPaths') if isinstance(registry.get('registryPaths'), dict) else {}
    target_paths = [Path(item).resolve() for item in list((registry_paths or {}).get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []) if str(item).strip()]
    provider_paths = [Path(item).resolve() for item in list((registry_paths or {}).get(DISPATCH_PROVIDER_REGISTRY_PATHS_KEY) or []) if str(item).strip()]
    if not target_paths:
        fail('collect-targets requires at least one dispatch target registry')
    env_map = env_loader(env_path)
    payload = dispatch_registry_loader(target_paths, provider_registry_path=provider_paths or None)
    _, targets = targets_payload_loader(payload, env=env_map)
    batch_id = str(opts.get('batch') or '').strip()
    allowed_ids: set[str] | None = None
    if batch_id:
        for row in list(((payload.get('verificationBatches') or {}).get('batches') or [])):
            if isinstance(row, dict) and str(row.get('id') or '').strip() == batch_id:
                allowed_ids = {str(item).strip() for item in list(row.get('targetIds') or []) if str(item).strip()}
                break
        if allowed_ids is None:
            fail(f'unknown verification batch: {batch_id}')
    selected: list[str] = []
    for target in targets:
        if allowed_ids is not None and target.target_id not in allowed_ids:
            continue
        if target.enabled or target.endpoint_present:
            selected.append(target.target_id)
    return ','.join(selected)


def verify_rotation_sequence(
    opts: dict[str, Any],
    *,
    config_path_resolver: Callable[[dict[str, Any]], Path | None],
    rotation_sequence_payload: Callable[..., dict[str, Any]],
    verify_target: Callable[[dict[str, Any]], tuple[dict[str, Any], int]],
    maybe_write_rotation_sequence_audit: Callable[..., Path | None],
    exit_code_from_status: Callable[..., int],
) -> tuple[dict[str, Any], int]:
    config_path = config_path_resolver(opts)
    push_run_id = str(opts.get('push_run_id') or '').strip()
    base_payload = rotation_sequence_payload(
        config_path=config_path,
        batch_id=str(opts.get('batch') or ''),
        targets_csv=str(opts.get('targets') or ''),
    )
    verification_rows: list[dict[str, Any]] = []
    operation_failures: list[str] = []
    for target_id in [str(item).strip() for item in list(base_payload.get('target_ids') or []) if str(item).strip()]:
        target_payload, _ = verify_target(
            {
                'target': target_id,
                'config_path': str(config_path or ''),
                'control_plane_profile': str(opts.get('control_plane_profile') or ''),
                'gate_env_file': str(opts.get('gate_env_file') or ''),
                'execution_surface': str(opts.get('execution_surface') or ''),
                'real_send': bool(opts.get('real_send')),
                'push_run_id': push_run_id,
                'skip_explain': bool(opts.get('skip_explain')),
                'skip_acceptance_summary': bool(opts.get('skip_acceptance_summary')),
                'fail_on_fail': False,
                'fail_on_warn': False,
            }
        )
        verification_rows.append(target_payload)
        operation_failures.extend(str(item).strip() for item in list(target_payload.get('operation_failures') or []) if str(item).strip())
        if target_payload.get('status') == 'fail' and not opts.get('keep_going'):
            break
    payload = rotation_sequence_payload(
        config_path=config_path,
        batch_id=str(base_payload.get('batch_id') or ''),
        targets_csv=','.join(str(item.get('target_id') or '').strip() for item in verification_rows if str(item.get('target_id') or '').strip()),
    )
    payload['verification_results'] = verification_rows
    payload['requested_real_send'] = bool(opts.get('real_send'))
    payload['push_run_id'] = push_run_id or None
    payload['keep_going'] = bool(opts.get('keep_going'))
    payload['operation_failures'] = operation_failures
    if operation_failures:
        payload['overall_status'] = 'fail'
    if opts.get('write_audit'):
        audit_path = maybe_write_rotation_sequence_audit(
            payload,
            config_path=Path(str(payload.get('config_path') or '')).resolve(),
            audit_dir=str(opts.get('audit_dir') or ''),
        )
        payload['audit_path'] = str(audit_path)
    return payload, exit_code_from_status(
        str(payload.get('overall_status') or ''),
        fail_on_warn=bool(opts.get('fail_on_warn')),
        fail_on_fail=bool(opts.get('fail_on_fail')),
    )
