from __future__ import annotations

import json
import shutil
from pathlib import Path

from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import ensure_repo_markers
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


def _copy_file(repo_root: Path, rel_path: str) -> None:
    source = ROOT_DIR / rel_path
    target = repo_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _write_json(repo_root: Path, rel_path: str, payload: object) -> None:
    target = repo_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def materialize_local_workspace_shell_repo(repo_root: Path) -> Path:
    repo_root = Path(repo_root).resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    for rel_path in (
        '.gitignore',
        'config/governance/support/local_workspace_policy.json',
        'config/governance/support/install_defaults.json',
        'scripts/lib/repo_root.sh',
        'scripts/lib/repo_contracts.sh',
        'scripts/lib/local_workspace_policy.sh',
        'scripts/lib/image_env.sh',
        'scripts/setup/lib/host_install_defaults.sh',
        'scripts/setup/lib/repo_root_bootstrap.sh',
        'scripts/setup/lib/runtime_permissions.sh',
        'scripts/setup/cleanup_local_workspace.sh',
        'scripts/setup/export_clean_delivery_bundle.sh',
        'scripts/setup/fix_permissions.sh',
        'scripts/doctor/check_local_workspace_hygiene.sh',
        'scripts/doctor/check_local_runtime_fs_contract.sh',
    ):
        _copy_file(repo_root, rel_path)
    _write_json(
        repo_root,
        'config/governance/support/repo_contracts.json',
        {
            'schemaVersion': 1,
            'contracts': [
                {
                    'id': 'governance.local_workspace_policy',
                    'relative_path': 'config/governance/support/local_workspace_policy.json',
                    'format': 'json',
                },
                {
                    'id': 'governance.install_defaults',
                    'relative_path': 'config/governance/support/install_defaults.json',
                    'format': 'json',
                },
                {
                    'id': 'governance.bundle_manifest',
                    'relative_path': 'config/governance/release/bundle_manifest.json',
                    'format': 'json',
                },
            ],
        },
    )
    launcher = repo_root / 'agent' / 'extensions' / 'agent_probe' / 'agent' / 'modules' / 'probe_worker' / 'bin' / 'probe_worker'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text('#!/usr/bin/env bash\nexit 0\n', encoding='utf-8')
    (repo_root / 'deploy' / 'nginx').mkdir(parents=True, exist_ok=True)
    (repo_root / 'config' / 'runtime').mkdir(parents=True, exist_ok=True)
    return repo_root


def materialize_managed_probe_repo(repo_root: Path) -> Path:
    repo_root = Path(repo_root).resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    ensure_repo_markers(repo_root, ROOT_DIR)
    return repo_root
