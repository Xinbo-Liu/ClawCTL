from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.control_plane.registry_loader import load_registry_from_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import ManagedExtensionRow, managed_explicit_extensions

ROOT_DIR = resolve_repo_root(Path(__file__))

def managed_extensions(start_path: Path | None = None) -> tuple[ManagedExtensionRow, ...]:
    repo_root = resolve_repo_root(ROOT_DIR if start_path is None else start_path)
    return managed_explicit_extensions(repo_root)


def representative_managed_extension(start_path: Path | None = None) -> ManagedExtensionRow:
    """返回一个确定性的受管扩展样本，用于不关心具体业务身份的测试。"""
    repo_root = resolve_repo_root(ROOT_DIR if start_path is None else start_path)
    extensions = managed_extensions(repo_root)
    if not extensions:
        raise AssertionError(f'expected at least one managed explicit extension in {repo_root}')
    return sorted(extensions, key=lambda row: row.id)[0]


def current_managed_extension(start_path: Path | None = None, *, extension_id: str | None = None) -> ManagedExtensionRow:
    repo_root = resolve_repo_root(ROOT_DIR if start_path is None else start_path)
    extensions = managed_extensions(repo_root)
    normalized_extension_id = str(extension_id or '').strip()
    if normalized_extension_id:
        for row in extensions:
            if row.id == normalized_extension_id:
                return row
        available = ', '.join(sorted(row.id for row in extensions)) or '<none>'
        raise AssertionError(
            f'managed explicit extension {normalized_extension_id} not found in {repo_root}; available: {available}'
        )
    if len(extensions) != 1:
        available = ', '.join(sorted(row.id for row in extensions)) or '<none>'
        raise AssertionError(
            f'expected exactly one managed explicit extension in {repo_root}, found {len(extensions)} ({available}); pass extension_id explicitly'
        )
    return extensions[0]


def current_managed_extension_domain_id(start_path: Path | None = None, *, extension_id: str | None = None) -> str:
    extension = current_managed_extension(start_path, extension_id=extension_id)
    shell_domain_root = extension.root_dir / 'agent' / 'domains'
    if shell_domain_root.is_dir():
        candidates = sorted(path.name for path in shell_domain_root.iterdir() if path.is_dir())
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            raise AssertionError(
                f'expected exactly one managed extension domain under {shell_domain_root}, found {len(candidates)}'
            )
    if extension.id.startswith('agent_'):
        return extension.id[len('agent_'):]
    return extension.id


def registry_rows(registry: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = registry.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def representative_managed_extension_registry(
    start_path: Path | None = None,
    *,
    extension_id: str | None = None,
) -> dict[str, Any]:
    extension = current_managed_extension(start_path, extension_id=extension_id) if extension_id else representative_managed_extension(start_path)
    return load_registry_from_path(extension.default_service_config_path)


def managed_extension_agent_ids(registry: dict[str, Any]) -> list[str]:
    return sorted(str(agent.get('id') or '').strip() for agent in registry_rows(registry, 'agents') if str(agent.get('id') or '').strip())


def cron_job_sort_key(job: dict[str, Any]) -> tuple[int, str]:
    try:
        order = int(job.get('resolvedOrder'))
    except (TypeError, ValueError):
        order = 100000
    return order, str(job.get('id') or '').strip()


def cron_jobs(registry: dict[str, Any], *, require_agent: bool = False) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in registry_rows(registry, 'jobs'):
        schedule = job.get('schedule') if isinstance(job.get('schedule'), dict) else {}
        has_required_agent = (not require_agent) or bool(str(job.get('agentRef') or '').strip())
        if str(job.get('id') or '').strip() and has_required_agent and str(schedule.get('kind') or 'cron').strip() == 'cron':
            jobs.append(job)
    return sorted(jobs, key=cron_job_sort_key)


def jobs_for_agent(registry: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    normalized = str(agent_id or '').strip()
    return [job for job in cron_jobs(registry, require_agent=True) if str(job.get('agentRef') or '').strip() == normalized]


def first_model_by_provider(registry: dict[str, Any], provider: str, *, apis: set[str] | None = None) -> dict[str, Any]:
    normalized_provider = str(provider or '').strip()
    allowed_apis = set(apis or ())
    for model in registry_rows(registry, 'models'):
        channel = model.get('channel') if isinstance(model.get('channel'), dict) else {}
        channel_api = str(channel.get('api') or '').strip()
        if str(model.get('provider') or '').strip() == normalized_provider and (not allowed_apis or channel_api in allowed_apis):
            return model
    raise AssertionError(f'expected representative managed extension to declare a {normalized_provider} model')


def single_json_object_id(root: Path, pattern: str) -> str:
    values = json_object_ids(root, pattern)
    if len(values) != 1:
        raise AssertionError(f'expected exactly one id for {pattern} under {root}, found {len(values)}')
    return values[0]


def json_object_ids(root: Path, pattern: str) -> list[str]:
    matches = sorted(root.glob(pattern))
    values: list[str] = []
    for path in matches:
        payload = json.loads(path.read_text(encoding='utf-8'))
        value = str(payload.get('id') or '').strip()
        if not value:
            raise AssertionError(f'missing id in {path}')
        values.append(value)
    if not values:
        raise AssertionError(f'expected at least one match for {pattern} under {root}, found 0')
    return values
