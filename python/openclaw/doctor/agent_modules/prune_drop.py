#!/usr/bin/env python3
"""在临时仓库副本中回归检查 agent 模块 prune/drop 流程。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from openclaw.control_plane.modules.lifecycle import (
    apply_agent_module_drop,
    apply_agent_module_prune,
    inspect_module_optional_surfaces,
    module_owned_surface_roots,
    plan_agent_module_drop,
    plan_agent_module_prune,
)
from openclaw.control_plane.modules.scaffold import scaffold_agent_module
from openclaw.control_plane.registry import load_registry
from openclaw.doctor.agent_modules.support import copy_repo_tree
from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.lib.repo.managed_extensions import managed_extension_for_config_path
from openclaw.lib.repo.layout import CONTROL_PLANE_CONFIG_ENV, CONTROL_PLANE_PROFILE_ENV, resolve_repo_root, resolve_selected_control_plane_config_path

ROOT_DIR = resolve_repo_root(Path(__file__))
PYTHON_PROBE_MODULE = '_'.join(['delta', 'probe'])


def _copy_repo(temp_root: Path) -> Path:
    return copy_repo_tree(ROOT_DIR, temp_root)


def usage() -> str:
    return '\n'.join([
        '用法:',
        '  python -m openclaw.doctor.agent_modules.prune_drop [--config-path <path>] [--control-plane-profile <profile_id>]',
        '',
        '行为:',
        '  在隔离仓库副本中运行 scaffold -> prune -> drop 回归检查。',
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
        sys.stderr.write(f'[check_agent_module_prune_drop][FAIL] {exc}\n')
        sys.stderr.write(f'{usage()}\n')
        raise SystemExit(2) from exc
    if positionals:
        sys.stderr.write(f'[check_agent_module_prune_drop][FAIL] 未知参数: {" ".join(positionals)}\n')
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
    registry = load_registry(config_path)
    return [
        str(item.get('id') or '').strip()
        for item in (registry.get('extensions') or [])
        if isinstance(item, dict) and str(item.get('id') or '').strip()
    ]


def _probe_owner_domain(config_path: Path) -> str:
    row = managed_extension_for_config_path(config_path, start_path=config_path)
    if row is not None:
        owner = row.id.replace('-', '_')
        if owner.startswith('agent_'):
            owner = owner[len('agent_'):]
        return owner or row.id.replace('-', '_')
    enabled_extension_ids = _enabled_extension_ids(config_path)
    if not enabled_extension_ids:
        raise AssertionError('prune/drop regression requires at least one enabled extension')
    owner = enabled_extension_ids[0].replace('-', '_')
    return 'platform' if owner == 'agent_platform' else owner


def _probe_filesystem_write(config_path: Path) -> list[str]:
    _ = config_path
    return []


def _scaffold_probe_modules(repo_root: Path, config_path: Path) -> None:
    owner_domain = _probe_owner_domain(config_path)
    filesystem_write = _probe_filesystem_write(config_path)
    scaffold_agent_module(
        repo_root=repo_root,
        config_path=config_path,
        module_ref=PYTHON_PROBE_MODULE,
        title='Delta Probe',
        owner_domain=owner_domain,
        operation_ref='probe_default',
        filesystem_write=filesystem_write,
        with_agents_doc=True,
        with_optional_dirs=True,
    )


def _optional_surface_state(registry: dict[str, Any], module_refs: set[str]) -> dict[str, list[str]]:
    return {
        str(module.get('id') or ''): [
            str(item.get('relPath') or '')
            for item in inspect_module_optional_surfaces(module)
            if bool(item.get('boilerplate'))
        ]
        for module in (registry.get('agentModules') or [])
        if isinstance(module, dict) and str(module.get('id') or '') in module_refs
    }


def _run_prune_probe(repo_root: Path, config_path: Path) -> dict[str, Any]:
    module_refs = {PYTHON_PROBE_MODULE}
    registry = load_registry(config_path)
    before_prune = _optional_surface_state(registry, module_refs)
    if not before_prune.get(PYTHON_PROBE_MODULE):
        raise AssertionError('scaffolded probe modules are missing optional boilerplate surfaces')

    prune_plan = plan_agent_module_prune(config_path=config_path, repo_root=repo_root, module_ref='')
    prune_apply = apply_agent_module_prune(config_path=config_path, repo_root=repo_root, module_ref='')

    registry = load_registry(config_path)
    after_prune = _optional_surface_state(registry, module_refs)
    if after_prune.get(PYTHON_PROBE_MODULE):
        raise AssertionError('prune left optional boilerplate surfaces behind')

    return {
        'prunePlanMode': prune_plan.get('mode'),
        'pruneApplyMode': prune_apply.get('mode'),
        'beforePrune': before_prune,
        'afterPrune': after_prune,
    }


def _run_drop_probe(
    *,
    repo_root: Path,
    config_path: Path,
    module_ref: str,
) -> dict[str, Any]:
    registry_before_drop = load_registry(config_path)
    module_row = (registry_before_drop.get('agentModulesById') or {}).get(module_ref, {})
    module_source_path = Path(str(module_row.get('sourcePath') or '')).resolve()
    module_payload = json.loads(module_source_path.read_text(encoding='utf-8'))
    owned_paths = module_owned_surface_roots(
        {**module_payload, 'sourcePath': str(module_source_path)},
        repo_root=repo_root,
        registry=registry_before_drop,
    )
    drop_plan = plan_agent_module_drop(config_path=config_path, repo_root=repo_root, module_ref=module_ref)
    drop_apply = apply_agent_module_drop(config_path=config_path, repo_root=repo_root, module_ref=module_ref)
    registry = load_registry(config_path)
    if module_ref in (registry.get('agentModulesById') or {}):
        raise AssertionError(f'{module_ref} still exists in registry after drop')
    for path in owned_paths:
        if path.exists():
            raise AssertionError(f'{module_ref} runtime source still exists after drop: {path}')
    return {
        'planMode': drop_plan.get('mode'),
        'applyMode': drop_apply.get('mode'),
    }


def _build_payload(prune_result: dict[str, Any], drop_results: dict[str, dict[str, Any]], config_path: Path) -> dict[str, Any]:
    registry = load_registry(config_path)
    return {
        'ok': True,
        'enabledExtensions': _enabled_extension_ids(config_path),
        'results': [
            {
                **prune_result,
                'deltaDropPlanMode': drop_results[PYTHON_PROBE_MODULE]['planMode'],
                'deltaDropApplyMode': drop_results[PYTHON_PROBE_MODULE]['applyMode'],
                'finalModuleCount': len(registry.get('agentModules') or []),
            }
        ],
    }


def _resolve_config_or_exit(
    repo_root: Path,
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> Path:
    try:
        return resolve_config_path(
            repo_root,
            requested_config_path,
            control_plane_profile=control_plane_profile,
        )
    except ValueError as exc:
        sys.stderr.write(f'[check_agent_module_prune_drop][FAIL] {exc}\n')
        raise SystemExit(2) from exc


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
    config_path = _resolve_config_or_exit(
        repo_root,
        _resolve_effective_requested_config_path(
            repo_root,
            requested_config_path,
            control_plane_profile=control_plane_profile,
        ),
        control_plane_profile=control_plane_profile,
    )
    _scaffold_probe_modules(repo_root, config_path)
    prune_result = _run_prune_probe(repo_root, config_path)
    drop_results = {
        PYTHON_PROBE_MODULE: _run_drop_probe(
            repo_root=repo_root,
            config_path=config_path,
            module_ref=PYTHON_PROBE_MODULE,
        ),
    }
    return _build_payload(prune_result, drop_results, config_path)


def _run_temp_probe(
    requested_config_path: Path | None,
    *,
    control_plane_profile: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='openclaw_prune_drop_') as temp_dir:
        repo_root = _copy_repo(Path(temp_dir))
        return _run_probe_in_repo_copy(
            repo_root,
            requested_config_path,
            control_plane_profile=control_plane_profile,
        )


def main(argv: list[str] | None = None) -> int:
    requested_config_path, control_plane_profile = parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        payload = _run_temp_probe(
            requested_config_path,
            control_plane_profile=control_plane_profile,
        )
    except SystemExit as exc:
        return int(exc.code)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
