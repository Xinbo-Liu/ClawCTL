#!/usr/bin/env python3
"""在临时仓库副本中回归检查 agent 模块 attach/detach 流程。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from openclaw.control_plane.jobs.surface import inspect_job_surface
from openclaw.control_plane.modules.scaffold import scaffold_agent_module
from openclaw.control_plane.module_scheduler.binding import (
    apply_agent_module_attach,
    apply_agent_module_detach,
    plan_agent_module_attach,
    plan_agent_module_detach,
)
from openclaw.control_plane.registry import load_registry
from openclaw.doctor.agent_modules.support import copy_repo_tree
from openclaw.doctor.platform.temp_workspace import global_tmp_root, keep_temp_dirs, make_temp_dir, prune_empty_parents, remove_tree
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.io.json_access import json_object
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.lib.repo.managed_extensions import managed_extension_for_config_path
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_repo_root, resolve_selected_control_plane_config_path

ROOT_DIR = resolve_repo_root(Path(__file__))


def _copy_repo(temp_root: Path) -> Path:
    return copy_repo_tree(ROOT_DIR, temp_root)


def usage() -> str:
    return '\n'.join([
        '用法:',
        '  python -m openclaw.doctor.agent_modules.attach_detach [--config-path <path>] [--control-plane-profile <profile_id>]',
        '',
        '行为:',
        '  在隔离仓库副本中运行 scaffold -> attach -> detach 回归检查。',
        '  零参数模式会向隔离仓库副本注入一个受管探测扩展。',
    ])


def parse_args(argv: list[str]) -> tuple[Path | None, str]:
    if any(arg in {'-h', '--help'} for arg in argv):
        sys.stdout.write(f'{usage()}\n')
        raise SystemExit(0)
    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'config-path': FlagSpec(kind='path', dest='config_path'),
                'control-plane-profile': FlagSpec(kind='str', dest='control_plane_profile'),
            },
        )
    except CliError as exc:
        sys.stderr.write(f'[check_agent_module_attach_detach][FAIL] {exc}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2) from exc
    if positionals:
        sys.stderr.write(f'[check_agent_module_attach_detach][FAIL] 未知参数: {" ".join(positionals)}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2)
    return values['config_path'], values['control_plane_profile'] or ''


def resolve_config_path(
    repo_root: Path,
    config_path: Path | None = None,
    *,
    control_plane_profile: str = '',
) -> Path:
    return resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile,
        start_path=repo_root,
        default_to_base=False,
    )


def _localize_requested_config_path(repo_root: Path, config_path: Path | None) -> Path | None:
    if config_path is None:
        return None
    candidate = Path(config_path)
    if not candidate.is_absolute():
        return (repo_root / candidate).resolve()
    try:
        relative = candidate.resolve().relative_to(ROOT_DIR.resolve())
    except ValueError:
        return candidate.resolve()
    return (repo_root / relative).resolve()


def _enabled_extension_ids(config_path: Path) -> list[str]:
    payload = load_registry(config_path)
    return [
        str(item.get('id') or '').strip()
        for item in (payload.get('extensions') or [])
        if isinstance(item, dict) and str(item.get('id') or '').strip()
    ]


def _probe_owner_domain(config_path: Path) -> str:
    row = managed_extension_for_config_path(config_path, start_path=config_path)
    if row is not None:
        owner = row.id.replace('-', '_')
        if owner.startswith('agent_'):
            owner = owner[len('agent_'):]
        return owner or row.id.replace('-', '_')
    enabled = _enabled_extension_ids(config_path)
    if not enabled:
        raise AssertionError('attach/detach regression requires at least one enabled extension')
    owner = enabled[0].replace('-', '_')
    return 'platform' if owner == 'agent_platform' else owner


def _module_state(registry: dict[str, Any], module_ref: str) -> dict[str, Any]:
    module = registry.get('agentModulesById', {}).get(module_ref)
    if not isinstance(module, dict):
        raise AssertionError(f'module not registered: {module_ref}')
    operations = json_object(module.get('operations'))
    job_refs: list[str] = []
    target_refs: list[str] = []
    for payload in operations.values():
        if not isinstance(payload, dict):
            continue
        job_bindings = json_object(payload.get('jobBindings'))
        for job_ref, binding in job_bindings.items():
            normalized_job_ref = str(job_ref or '').strip()
            if normalized_job_ref and normalized_job_ref not in job_refs:
                job_refs.append(normalized_job_ref)
            if isinstance(binding, dict):
                target_ref = str(binding.get('targetBindingRef') or '').strip()
                if target_ref and target_ref not in target_refs:
                    target_refs.append(target_ref)
    return {
        'bindingMode': 'scheduler_bound' if job_refs else 'standalone',
        'jobRefs': job_refs,
        'targetBindingRefs': target_refs,
        'groupRefs': [str(item).strip() for item in (module.get('resolvedGroupRefs') or []) if str(item).strip()],
    }


def _probe_scaffold_kwargs(config_path: Path) -> dict[str, Any]:
    return {
        'module_ref': 'lifecycle_probe',
        'title': 'Lifecycle Probe',
        'owner_domain': _probe_owner_domain(config_path),
        'operation_ref': 'probe_default',
    }


def _probe_attach_kwargs() -> dict[str, Any]:
    return {
        'operation_ref': 'probe_default',
        'job_id': 'lifecycle_probe_weekday',
        'job_title': 'Lifecycle Probe Check',
        'schedule_expr': '0 10 * * 1-5',
        'schedule_tz': 'Asia/Shanghai',
        'group_ref': '',
        'group_placement': 'none',
        'before_job': '',
        'after_job': '',
        'recovery_after_minutes': None,
        'recovery_action_kind': 'retry',
        'target_binding_ref': '',
        'depends_on': [],
        'order': None,
        'timeout_seconds': None,
        'retry_enabled': None,
        'retry_max_attempts': None,
        'retry_backoff_seconds': [],
        'run_artifact_root': 'control_plane/lifecycle_probe',
        'latest_alias': '',
        'retention_days': 14,
        'retryable_classes': [],
        'terminal_classes': ['lifecycle_probe_failed'],
        'model_profile_ref': '',
        'job_file_prefix': '',
    }


def _verify_after_attach(registry: dict[str, Any], state: dict[str, Any], attach_kwargs: dict[str, Any]) -> None:
    job_id = str(attach_kwargs['job_id'])
    if state['bindingMode'] != 'scheduler_bound':
        raise AssertionError('lifecycle_probe attach did not enter scheduler_bound state')
    if job_id not in state['jobRefs']:
        raise AssertionError('lifecycle_probe attach did not bind the scheduled job')
    if state['groupRefs']:
        raise AssertionError('lifecycle_probe attach unexpectedly joined a group')
    if state['targetBindingRefs']:
        raise AssertionError('lifecycle_probe attach unexpectedly bound a target')
    job = registry.get('jobsById', {}).get(job_id, {})
    if str(job.get('groupRef') or '').strip():
        raise AssertionError('lifecycle_probe attach unexpectedly persisted groupRef on job')
    if str(job.get('targetBindingRef') or '').strip():
        raise AssertionError('lifecycle_probe attach unexpectedly persisted targetBindingRef on job')


def _verify_after_detach(registry: dict[str, Any], state: dict[str, Any], attach_kwargs: dict[str, Any]) -> None:
    job_id = str(attach_kwargs['job_id'])
    if state['bindingMode'] != 'standalone':
        raise AssertionError('lifecycle_probe detach did not return to standalone mode')
    if job_id in registry.get('jobsById', {}):
        raise AssertionError('lifecycle_probe detach left the scheduled job behind')


def _run_attach_detach_probe(*, repo_root: Path, config_path: Path) -> dict[str, Any]:
    scaffold_kwargs = _probe_scaffold_kwargs(config_path)
    attach_kwargs = _probe_attach_kwargs()
    module_ref = str(scaffold_kwargs['module_ref'])
    operation_ref = str(attach_kwargs['operation_ref'])
    job_id = str(attach_kwargs['job_id'])

    scaffold_agent_module(repo_root=repo_root, config_path=config_path, **scaffold_kwargs)
    attach_plan = plan_agent_module_attach(config_path=config_path, repo_root=repo_root, module_ref=module_ref, **attach_kwargs)
    attach_apply = apply_agent_module_attach(config_path=config_path, repo_root=repo_root, module_ref=module_ref, **attach_kwargs)

    registry = load_registry(config_path)
    state_after_attach = _module_state(registry, module_ref)
    _verify_after_attach(registry, state_after_attach, attach_kwargs)
    job_surface = inspect_job_surface(registry['jobsById'][job_id], registry=registry)
    if not bool(job_surface.get('ok')):
        raise AssertionError(f'{module_ref} attach left drift paths: {job_surface.get("driftPaths")}')

    detach_plan = plan_agent_module_detach(
        config_path=config_path,
        repo_root=repo_root,
        module_ref=module_ref,
        job_id=job_id,
        operation_ref=operation_ref,
    )
    detach_apply = apply_agent_module_detach(
        config_path=config_path,
        repo_root=repo_root,
        module_ref=module_ref,
        job_id=job_id,
        operation_ref=operation_ref,
    )

    registry = load_registry(config_path)
    state_after_detach = _module_state(registry, module_ref)
    _verify_after_detach(registry, state_after_detach, attach_kwargs)
    return {
        'moduleRef': module_ref,
        'ownerDomain': str(scaffold_kwargs['owner_domain']),
        'attachPlanMode': attach_plan.get('mode'),
        'attachApplyMode': attach_apply.get('mode'),
        'afterAttach': state_after_attach,
        'jobSurface': {
            'ok': bool(job_surface.get('ok')),
            'driftPaths': list(job_surface.get('driftPaths') or []),
        },
        'detachPlanMode': detach_plan.get('mode'),
        'detachApplyMode': detach_apply.get('mode'),
        'afterDetach': state_after_detach,
    }


def _build_payload(result: dict[str, Any], config_path: Path) -> dict[str, Any]:
    final_registry = load_registry(config_path)
    return {
        'ok': True,
        'enabledExtensions': _enabled_extension_ids(config_path),
        'results': [result],
        'finalCounts': {
            'agentModules': len(final_registry.get('agentModules', [])),
            'jobs': len(final_registry.get('jobs', [])),
            'targets': len(final_registry.get('targets', [])),
        },
    }


def _cleanup_temp_dir(temp_dir: Path) -> Exception | None:
    if keep_temp_dirs('OPENCLAW_KEEP_DOCTOR_TMP'):
        print(f'[check_agent_module_attach_detach][INFO] keep temp dir: {temp_dir}', file=sys.stderr)
        return None
    try:
        remove_tree(temp_dir)
        prune_empty_parents(temp_dir.parent, stop_at=global_tmp_root())
    except Exception as exc:
        # 清理失败只影响临时目录卫生，不能掩盖 attach/detach 主检查结果。
        print(f'[check_agent_module_attach_detach][WARN] temp dir cleanup failed: {temp_dir} ({exc})', file=sys.stderr)
        return exc
    return None


def _default_probe_config_path(repo_root: Path) -> Path:
    return materialize_managed_probe_extension(repo_root, base_repo_root=repo_root).service_path


def _resolve_effective_requested_config_path(
    repo_root: Path,
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> Path | None:
    effective_requested_config_path = _localize_requested_config_path(repo_root, requested_config_path)
    if (
        effective_requested_config_path is None
        and not control_plane_profile
        and not str(os.environ.get(CONTROL_PLANE_CONFIG_ENV) or '').strip()
        and not str(os.environ.get(CONTROL_PLANE_PROFILE_ENV) or '').strip()
    ):
        return _default_probe_config_path(repo_root)
    return effective_requested_config_path


def _run_probe_in_repo_copy(
    repo_root: Path,
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> dict[str, Any]:
    try:
        config_path = resolve_config_path(
            repo_root,
            _resolve_effective_requested_config_path(
                repo_root,
                requested_config_path,
                control_plane_profile=control_plane_profile,
            ),
            control_plane_profile=control_plane_profile,
        )
    except ValueError as exc:
        sys.stderr.write(f'[check_agent_module_attach_detach][FAIL] {exc}\n')
        raise SystemExit(2) from exc
    result = _run_attach_detach_probe(repo_root=repo_root, config_path=config_path)
    return _build_payload(result, config_path)


def _run_temp_probe(
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='openclaw_attach_detach_') as temp_dir:
        repo_root = _copy_repo(Path(temp_dir))
        return _run_probe_in_repo_copy(
            repo_root,
            requested_config_path,
            control_plane_profile=control_plane_profile,
        )


def main(argv: list[str] | None = None) -> int:
    requested_config_path, control_plane_profile = parse_args(list(sys.argv[1:] if argv is None else argv))
    temp_dir = make_temp_dir(ROOT_DIR, category='doctor', prefix='openclaw_attach_detach')
    try:
        repo_root = _copy_repo(temp_dir)
        try:
            payload = _run_probe_in_repo_copy(
                repo_root,
                requested_config_path,
                control_plane_profile=control_plane_profile,
            )
        except SystemExit as exc:
            return int(exc.code)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        cleanup_error = _cleanup_temp_dir(temp_dir)
    return 1 if cleanup_error is not None else 0


if __name__ == '__main__':
    raise SystemExit(main())
