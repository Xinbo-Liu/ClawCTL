"""Gateway workspace 与 agent 文件派生产物。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any, Dict, List
import time
import uuid

from openclaw.lib.runtime.path_resolver import PathResolver

from ..constants import RENDER_GENERATED_RUNTIME_PATHS_CMD
from ..io import read_json, read_text, write_text
from ..registry import _load_registry, _registry_rows
from ..shared import _json_object, _line_text, _markdown_inline_values, _positive_int, _string_rows, _text
from .constants import (
    GATEWAY_AGENT_CORE_FILE_NAMES,
    GATEWAY_AGENT_INTERACTIVE_DEFAULTS,
    GATEWAY_DEFAULT_SESSION_LABEL,
    GATEWAY_DEFAULT_SESSION_UUID_PREFIX,
    GATEWAY_HEALTHCHECK_SCRIPT_SOURCE_REL,
    GATEWAY_HEALTHCHECK_SCRIPT_STATE_REL,
    GATEWAY_MAIN_AGENT_ID,
    GATEWAY_MAIN_AGENT_NAME,
    GATEWAY_ROUTER_WORKSPACE_ENTRY_ID,
    GATEWAY_ROUTER_WORKSPACE_ID,
)
from .cron import _cron_schedule, _cron_sort_key


def _gateway_agent_interactive_defaults() -> Dict[str, Any]:
    return deepcopy(GATEWAY_AGENT_INTERACTIVE_DEFAULTS)


def gateway_healthcheck_script_source_path(repo_root: Path) -> Path:
    return repo_root / GATEWAY_HEALTHCHECK_SCRIPT_SOURCE_REL


def gateway_healthcheck_script_targets(repo_root: Path, resolver: PathResolver) -> Dict[Path, str]:
    target = resolver.absolute_host_path('gateway_host_state_dir') / GATEWAY_HEALTHCHECK_SCRIPT_STATE_REL
    return {target: read_text(gateway_healthcheck_script_source_path(repo_root))}


def _gateway_agent_workspace_path(agent_id: str, resolver: PathResolver) -> str:
    entry_id = f'workspace_{agent_id}'
    if entry_id in resolver.entries:
        return _text(resolver.resolve_entry(entry_id)['paths'].get('gateway'))
    return f"{resolver.resolve_path('state_root', 'gateway')}/workspace-{agent_id}"


def _gateway_agent_workspace_host_path(agent_id: str, resolver: PathResolver) -> Path:
    entry_id = f'workspace_{agent_id}'
    if entry_id in resolver.entries:
        return resolver.absolute_host_path(entry_id)
    return resolver.absolute_host_path('gateway_host_state_dir') / f'workspace-{agent_id}'


def _gateway_router_workspace_path(resolver: PathResolver) -> str:
    if GATEWAY_ROUTER_WORKSPACE_ENTRY_ID in resolver.entries:
        return _text(resolver.resolve_entry(GATEWAY_ROUTER_WORKSPACE_ENTRY_ID)['paths'].get('gateway'))
    return f"{resolver.resolve_path('state_root', 'gateway')}/workspace-{GATEWAY_ROUTER_WORKSPACE_ID}"


def _gateway_router_workspace_host_path(resolver: PathResolver) -> Path:
    if GATEWAY_ROUTER_WORKSPACE_ENTRY_ID in resolver.entries:
        return resolver.absolute_host_path(GATEWAY_ROUTER_WORKSPACE_ENTRY_ID)
    return resolver.absolute_host_path('gateway_host_state_dir') / f'workspace-{GATEWAY_ROUTER_WORKSPACE_ID}'


def _gateway_agent_dir_path(agent_id: str, resolver: PathResolver) -> str:
    return f"{resolver.resolve_path('state_root', 'gateway')}/agents/{agent_id}/agent"


def _gateway_agent_dir_host_path(agent_id: str, resolver: PathResolver) -> Path:
    return resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / agent_id / 'agent'


def _gateway_agent_sessions_path(agent_id: str, resolver: PathResolver) -> str:
    return f"{resolver.resolve_path('state_root', 'gateway')}/agents/{agent_id}/sessions"


def _gateway_agent_sessions_host_path(agent_id: str, resolver: PathResolver) -> Path:
    return resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / agent_id / 'sessions'


def build_gateway_agent_projection(registry: Dict[str, Any], resolver: PathResolver) -> List[Dict[str, Any]]:
    business_agents: List[Dict[str, Any]] = []
    for agent in sorted(_registry_rows(registry, 'agents'), key=lambda row: _text(row.get('id'))):
        agent_id = _text(agent.get('id'))
        if not agent_id:
            continue
        title = _text(agent.get('title')) or agent_id
        entry: Dict[str, Any] = {
            'id': agent_id,
            'name': title,
            'workspace': _gateway_agent_workspace_path(agent_id, resolver),
            'agentDir': _gateway_agent_dir_path(agent_id, resolver),
            'identity': {'name': title},
            **_gateway_agent_interactive_defaults(),
        }
        business_agents.append(entry)
    if not business_agents:
        return []
    business_agent_ids = [_text(agent['id']) for agent in business_agents]
    router_agent: Dict[str, Any] = {
        'id': GATEWAY_MAIN_AGENT_ID,
        'name': GATEWAY_MAIN_AGENT_NAME,
        'workspace': _gateway_router_workspace_path(resolver),
        'agentDir': _gateway_agent_dir_path(GATEWAY_MAIN_AGENT_ID, resolver),
        'identity': {'name': GATEWAY_MAIN_AGENT_NAME},
        'default': True,
        'subagents': {
            'allowAgents': business_agent_ids,
            'requireAgentId': True,
        },
        **_gateway_agent_interactive_defaults(),
    }
    return [router_agent, *business_agents]


def _agent_jobs(registry: Dict[str, Any], agent_id: str) -> List[Dict[str, Any]]:
    return sorted(
        [job for job in _registry_rows(registry, 'jobs') if _text(job.get('agentRef')) == agent_id],
        key=_cron_sort_key,
    )


def _agent_runtime_module(agent: Dict[str, Any]) -> str:
    runtime = _json_object(agent.get('resolvedRuntime'))
    config = _json_object(runtime.get('config'))
    return _line_text(config.get('module'))


def _agent_entrypoint_kind(agent: Dict[str, Any]) -> str:
    return _line_text(_json_object(agent.get('entrypoint')).get('kind'))


def _agent_runtime_adapter(agent: Dict[str, Any]) -> str:
    runtime = _json_object(agent.get('resolvedRuntime'))
    return _line_text(agent.get('resolvedRuntimeAdapterRef')) or _line_text(runtime.get('adapterRef'))


def _agent_model_profile(agent: Dict[str, Any]) -> str:
    return _line_text(agent.get('defaultModelProfileRef')) or '-'


def _agent_capability_lines(agent: Dict[str, Any]) -> str:
    capabilities = _json_object(agent.get('capabilities'))
    if not capabilities:
        return '- 无'
    lines: List[str] = []
    for key in sorted(capabilities):
        value = capabilities[key]
        if isinstance(value, bool):
            formatted = 'true' if value else 'false'
        elif isinstance(value, list):
            formatted = _markdown_inline_values(_string_rows(value))
        elif isinstance(value, dict):
            formatted = '`' + json.dumps(value, ensure_ascii=False, sort_keys=True) + '`'
        else:
            formatted = _line_text(value) or '-'
        lines.append(f'- `{key}`: {formatted}')
    return '\n'.join(lines)


def _agent_io_lines(section: Dict[str, Any]) -> str:
    rows: List[str] = []
    for key in ('artifacts', 'runtimeInputs', 'statusSignals'):
        values = _string_rows(section.get(key))
        if values:
            rows.append(f'- `{key}`: {_markdown_inline_values(values)}')
    notes = _string_rows(section.get('notes'))
    if notes:
        rows.append(f'- `notes`: {_markdown_inline_values(notes, code=False)}')
    return '\n'.join(rows) if rows else '- 无'


def _agent_job_lines(jobs: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for job in jobs:
        job_id = _line_text(job.get('id'))
        if not job_id:
            continue
        schedule = _cron_schedule(job)
        schedule_label = f"{schedule['kind']} {schedule['expr']} {schedule['tz']}".strip()
        title = _line_text(job.get('title')) or job_id
        operation = _line_text(job.get('operationRef')) or '-'
        timeout = _positive_int(job.get('timeoutSeconds'), default=1800)
        enabled = 'true' if bool(job.get('enabled', True)) else 'false'
        rows.append(
            f'- `{job_id}`: {title}; schedule `{schedule_label}`; operation `{operation}`; '
            f'timeout `{timeout}` 秒; enabled `{enabled}`。'
        )
    return '\n'.join(rows) if rows else '- 无'


def _gateway_agent_file_header(title: str, agent_id: str) -> List[str]:
    return [
        f'# {title}',
        '',
        f'> 由 `{RENDER_GENERATED_RUNTIME_PATHS_CMD}` 根据 active control-plane registry 生成；Gateway UI 使用该文件展示 `{agent_id}`。',
        '',
    ]


def build_gateway_agent_core_files(
    agent: Dict[str, Any],
    registry: Dict[str, Any],
    resolver: PathResolver,
) -> Dict[str, str]:
    agent_id = _line_text(agent.get('id'))
    title = _line_text(agent.get('title')) or agent_id
    description = _line_text(agent.get('description')) or '-'
    workspace = _gateway_agent_workspace_path(agent_id, resolver)
    agent_dir = _gateway_agent_dir_path(agent_id, resolver)
    jobs = _agent_jobs(registry, agent_id)
    job_lines = _agent_job_lines(jobs)
    groups = _string_rows(agent.get('resolvedGroupRefs'))
    executors = _string_rows(agent.get('allowedExecutorKinds'))
    inputs = _agent_io_lines(_json_object(agent.get('resolvedInputs')))
    outputs = _agent_io_lines(_json_object(agent.get('resolvedOutputs')))
    runtime_module = _agent_runtime_module(agent) or '-'
    runtime_adapter = _agent_runtime_adapter(agent) or '-'
    entrypoint_kind = _agent_entrypoint_kind(agent) or '-'

    common = _gateway_agent_file_header(title, agent_id)
    files = {
        'IDENTITY.md': [
            *common,
            '## 身份',
            f'- Agent ID: `{agent_id}`',
            f'- 名称: {title}',
            f'- 角色: {description}',
            f'- 分组: {_markdown_inline_values(groups)}',
            f'- 默认模型: `{_agent_model_profile(agent)}`',
            f'- Gateway workspace: `{workspace}`',
            f'- Gateway agentDir: `{agent_dir}`',
            '',
        ],
        'AGENTS.md': [
            *common,
            '## 工作方式',
            '- control-plane registry 是业务配置、执行入口、输入输出与调度任务的真源。',
            '- Gateway workspace 承载 UI 可见的 agent 文件面；业务执行由 scheduler 视角和 runtime adapter 承担。',
            f'- runtime adapter: `{runtime_adapter}`',
            f'- entrypoint kind: `{entrypoint_kind}`',
            f'- runtime module: `{runtime_module}`',
            '',
            '## 输入',
            inputs,
            '',
            '## 输出',
            outputs,
            '',
            '## 定时任务',
            job_lines,
            '',
        ],
        'SOUL.md': [
            *common,
            '## 定位',
            description,
            '',
            '## 准则',
            '- 按 registry 中的输入输出合同解释任务边界。',
            '- 只把 Gateway UI 作为控制与展示面，不把 UI 工作区文件作为业务执行状态真源。',
            '- 运行结果以 scheduler 侧产物目录、审计文件和状态信号为准。',
            '',
        ],
        'TOOLS.md': [
            *common,
            '## 执行器',
            f'- entrypoint kind: `{entrypoint_kind}`',
            f'- allowed executor kinds: {_markdown_inline_values(executors)}',
            f'- runtime adapter: `{runtime_adapter}`',
            f'- runtime module: `{runtime_module}`',
            '',
            '## 能力',
            _agent_capability_lines(agent),
            '',
        ],
        'USER.md': [
            *common,
            '## 使用边界',
            '- 在 Gateway UI 中查看 agent 身份、工作区文件、skills、tools 与定时任务展示。',
            '- 对业务运行状态的判断以 control-plane 与 scheduler 运行态产物为准。',
            '- 对外发送、网络访问和文件写入能力按 `TOOLS.md` 中的 registry 能力声明解释。',
            '',
            '## 可见任务',
            job_lines,
            '',
        ],
        'HEARTBEAT.md': [
            *common,
            '## 状态信号',
            outputs,
            '',
            '## 调度节奏',
            job_lines,
            '',
        ],
        'BOOTSTRAP.md': [
            *common,
            '## 启动读取顺序',
            '- `IDENTITY.md`',
            '- `AGENTS.md`',
            '- `TOOLS.md`',
            '- `USER.md`',
            '- `HEARTBEAT.md`',
            '- `MEMORY.md`',
            '',
            '## 运行入口',
            f'- runtime adapter: `{runtime_adapter}`',
            f'- entrypoint kind: `{entrypoint_kind}`',
            f'- runtime module: `{runtime_module}`',
            '- 定时执行入口由 control-plane scheduler 解析 job 与 operation。',
            '',
        ],
        'MEMORY.md': [
            *common,
            '## 当前记忆',
            '- Gateway workspace 的该文件面由 active registry 生成。',
            '- 长期业务状态、运行历史与审计记录保存在 scheduler/control-plane 对应产物目录。',
            '',
            '## 已知上下文',
            f'- 输入: {_markdown_inline_values(_string_rows(_json_object(agent.get("resolvedInputs")).get("artifacts")))}',
            f'- 输出: {_markdown_inline_values(_string_rows(_json_object(agent.get("resolvedOutputs")).get("artifacts")))}',
            f'- 分组: {_markdown_inline_values(groups)}',
            '',
        ],
    }
    return {name: '\n'.join(files[name]) for name in GATEWAY_AGENT_CORE_FILE_NAMES}


def gateway_agent_core_file_targets(registry: Dict[str, Any], resolver: PathResolver) -> Dict[Path, str]:
    targets: Dict[Path, str] = {}
    for agent in sorted(_registry_rows(registry, 'agents'), key=lambda row: _text(row.get('id'))):
        agent_id = _line_text(agent.get('id'))
        if not agent_id:
            continue
        workspace_path = _gateway_agent_workspace_host_path(agent_id, resolver)
        agent_dir_path = _gateway_agent_dir_host_path(agent_id, resolver)
        for filename, content in build_gateway_agent_core_files(agent, registry, resolver).items():
            targets[workspace_path / filename] = content
            targets[agent_dir_path / filename] = content
    return targets


def gateway_router_workspace_file_targets(repo_root: Path, resolver: PathResolver) -> Dict[Path, str]:
    template_dir = repo_root / 'config' / 'workspace_templates' / GATEWAY_ROUTER_WORKSPACE_ID
    if not template_dir.exists():
        return {}
    workspace_path = _gateway_router_workspace_host_path(resolver)
    targets: Dict[Path, str] = {}
    for source_path in sorted(path for path in template_dir.rglob('*') if path.is_file()):
        targets[workspace_path / source_path.relative_to(template_dir)] = read_text(source_path)
    return targets


def gateway_router_agent_file_targets(repo_root: Path, resolver: PathResolver) -> Dict[Path, str]:
    template_dir = repo_root / 'config' / 'workspace_templates' / GATEWAY_ROUTER_WORKSPACE_ID
    if not template_dir.exists():
        return {}
    agent_dir_path = _gateway_agent_dir_host_path(GATEWAY_MAIN_AGENT_ID, resolver)
    targets: Dict[Path, str] = {}
    for source_path in sorted(path for path in template_dir.rglob('*') if path.is_file()):
        targets[agent_dir_path / source_path.relative_to(template_dir)] = read_text(source_path)
    return targets


def _gateway_default_session_id(agent_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{GATEWAY_DEFAULT_SESSION_UUID_PREFIX}:{agent_id}:{GATEWAY_DEFAULT_SESSION_LABEL}'))


def _gateway_default_session_key(agent_id: str) -> str:
    return f'agent:{agent_id}:{GATEWAY_DEFAULT_SESSION_LABEL}'


def _gateway_default_session_seed_time_ms() -> int:
    return max(1, int(time.time() * 1000))


def _gateway_default_session_metadata(agent_id: str, resolver: PathResolver, *, seed_time_ms: int | None = None) -> Dict[str, Any]:
    session_id = _gateway_default_session_id(agent_id)
    seed_time = seed_time_ms if seed_time_ms is not None else _gateway_default_session_seed_time_ms()
    return {
        'sessionId': session_id,
        'updatedAt': seed_time,
        'sessionStartedAt': seed_time,
        'lastInteractionAt': seed_time,
        'systemSent': False,
        'abortedLastRun': False,
        'chatType': 'direct',
        'sessionFile': f'{_gateway_agent_sessions_path(agent_id, resolver)}/{session_id}.jsonl',
        'deliveryContext': {
            'channel': 'webchat',
        },
        'lastChannel': 'webchat',
        'status': 'done',
        'origin': {
            'provider': 'webchat',
            'surface': 'webchat',
            'chatType': 'direct',
        },
    }


def gateway_default_session_targets(registry: Dict[str, Any], resolver: PathResolver, *, seed_time_ms: int | None = None) -> Dict[Path, Dict[str, Dict[str, Any]]]:
    targets: Dict[Path, Dict[str, Dict[str, Any]]] = {}
    seed_time = seed_time_ms if seed_time_ms is not None else _gateway_default_session_seed_time_ms()
    for agent in build_gateway_agent_projection(registry, resolver):
        agent_id = _line_text(agent.get('id'))
        if not agent_id:
            continue
        targets[_gateway_agent_sessions_host_path(agent_id, resolver) / 'sessions.json'] = {
            _gateway_default_session_key(agent_id): _gateway_default_session_metadata(agent_id, resolver, seed_time_ms=seed_time),
        }
    return targets


def gateway_default_session_transcript_targets(registry: Dict[str, Any], resolver: PathResolver) -> List[Path]:
    targets: List[Path] = []
    for agent in build_gateway_agent_projection(registry, resolver):
        agent_id = _line_text(agent.get('id'))
        if not agent_id:
            continue
        targets.append(_gateway_agent_sessions_host_path(agent_id, resolver) / f'{_gateway_default_session_id(agent_id)}.jsonl')
    return targets


def gateway_agent_state_dir_targets(registry: Dict[str, Any], resolver: PathResolver) -> List[Path]:
    targets: List[Path] = []
    if _registry_rows(registry, 'agents'):
        main_agent_root = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / GATEWAY_MAIN_AGENT_ID
        targets.extend([
            _gateway_router_workspace_host_path(resolver),
            main_agent_root / 'agent',
            main_agent_root / 'sessions',
        ])
    for agent in sorted(_registry_rows(registry, 'agents'), key=lambda row: _text(row.get('id'))):
        agent_id = _line_text(agent.get('id'))
        if not agent_id:
            continue
        agent_root = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / agent_id
        targets.extend([
            _gateway_agent_workspace_host_path(agent_id, resolver),
            agent_root / 'agent',
            agent_root / 'sessions',
        ])
    return targets


def _gateway_agent_root_paths(registry: Dict[str, Any], resolver: PathResolver) -> set[Path]:
    """返回当前 registry 允许保留的 Gateway agent 根目录集合。

    参数：
        registry（Dict[str, Any]）：控制面 registry，用来确定 Gateway 可见 agent 清单。
        resolver（PathResolver）：运行态路径解析器，用来把 Gateway state 根解析为宿主机绝对路径。

    返回：
        返回 set[Path]，表示当前 registry 声明的 Gateway agent 根目录集合。
    """
    projection = build_gateway_agent_projection(registry, resolver)
    gateway_root = resolver.absolute_host_path('gateway_host_state_dir')
    return {
        gateway_root / 'agents' / _line_text(agent.get('id'))
        for agent in projection
        if _line_text(agent.get('id'))
    }


def _gateway_workspace_paths(registry: Dict[str, Any], resolver: PathResolver) -> set[Path]:
    """返回当前 registry 允许保留的 Gateway workspace 目录集合。

    参数：
        registry（Dict[str, Any]）：控制面 registry，用来生成 Gateway agent/workspace 投影。
        resolver（PathResolver）：运行态路径解析器，用来定位 Gateway state 下的 workspace 目录。

    返回：
        返回 set[Path]，表示当前 registry 声明的 Gateway workspace 目录集合。
    """
    projection = build_gateway_agent_projection(registry, resolver)
    return {
        _gateway_router_workspace_host_path(resolver)
        if _line_text(agent.get('id')) == GATEWAY_MAIN_AGENT_ID
        else _gateway_agent_workspace_host_path(_line_text(agent.get('id')), resolver)
        for agent in projection
        if _line_text(agent.get('id'))
    }


def _contains_gateway_generated_marker(path: Path) -> bool:
    """判断目录是否包含 Gateway 运行态生成器写出的核心文件。

    参数：
        path（Path）：待检查目录；函数会读取目录或其 `agent/` 子目录下的核心文件。

    返回：
        返回 bool，表示候选目录是否能安全认定为 Gateway 派生产物。
    """
    candidate_roots = [path, path / 'agent']
    for root in candidate_roots:
        for filename in GATEWAY_AGENT_CORE_FILE_NAMES:
            candidate = root / filename
            if not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            if RENDER_GENERATED_RUNTIME_PATHS_CMD in content:
                return True
    return False


def _remove_tree_under_gateway_root(gateway_root: Path, target: Path) -> None:
    """删除 Gateway state 根内的单个派生目录。

    参数：
        gateway_root（Path）：Gateway state 根目录；删除边界必须落在该目录内部。
        target（Path）：待删除的派生目录或符号链接。

    异常：
        当目标不在 Gateway state 根内或目标就是根目录时抛出 ValueError。

    副作用：
        删除 Gateway state 根内已确认为派生产物的目录或符号链接。
    """
    root_resolved = gateway_root.resolve()
    target_resolved = target.resolve()
    try:
        relative = target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f'Gateway stale state target escapes state root: {target}') from exc
    if not relative.parts:
        raise ValueError('Gateway stale state cleanup refused to remove state root')
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def stale_gateway_agent_state_dirs(registry: Dict[str, Any], resolver: PathResolver) -> List[Path]:
    """列出当前 registry 之外的 Gateway agent/workspace 派生目录。

    参数：
        registry（Dict[str, Any]）：当前控制面 registry，用来计算仍应保留的 Gateway 目录。
        resolver（PathResolver）：运行态路径解析器，用来定位 Gateway state、agent 和 workspace 根。

    返回：
        返回 List[Path]，表示可由运行态生成器清理的旧 Gateway 派生目录。
    """
    gateway_root = resolver.absolute_host_path('gateway_host_state_dir')
    expected_agent_roots = {path.resolve() for path in _gateway_agent_root_paths(registry, resolver)}
    expected_workspaces = {path.resolve() for path in _gateway_workspace_paths(registry, resolver)}
    stale: List[Path] = []

    agents_root = gateway_root / 'agents'
    if agents_root.is_dir():
        for child in sorted(agents_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or child.resolve() in expected_agent_roots:
                continue
            if _contains_gateway_generated_marker(child):
                stale.append(child)

    if gateway_root.is_dir():
        for child in sorted(gateway_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or not child.name.startswith('workspace-'):
                continue
            if child.resolve() in expected_workspaces:
                continue
            if _contains_gateway_generated_marker(child):
                stale.append(child)
    return stale


def prune_stale_gateway_agent_state_dirs(registry: Dict[str, Any], resolver: PathResolver) -> List[Path]:
    """按当前 registry 删除旧 Gateway agent/workspace 派生目录。

    参数：
        registry（Dict[str, Any]）：当前控制面 registry，用来判断哪些派生目录已经不再属于有效 agent。
        resolver（PathResolver）：运行态路径解析器，用来定位 Gateway state 根和待清理目录。

    返回：
        返回 List[Path]，表示本次删除的旧 Gateway 派生目录。
    """
    gateway_root = resolver.absolute_host_path('gateway_host_state_dir')
    removed = stale_gateway_agent_state_dirs(registry, resolver)
    for path in removed:
        _remove_tree_under_gateway_root(gateway_root, path)
    return removed


def _gateway_default_session_has_empty_transcript(session_store_path: Path, payload: Dict[str, Any]) -> bool:
    session_id = _line_text(payload.get('sessionId'))
    if not session_id:
        return True
    transcript_path = session_store_path.parent / f'{session_id}.jsonl'
    try:
        return (not transcript_path.exists()) or transcript_path.stat().st_size == 0
    except OSError:
        return True


def render_gateway_default_sessions(registry: Dict[str, Any], resolver: PathResolver) -> None:
    visibility_fields = {'deliveryContext', 'lastChannel', 'origin'}
    seed_time_fields = {'updatedAt', 'sessionStartedAt', 'lastInteractionAt'}
    for path, seeded_entries in gateway_default_session_targets(registry, resolver).items():
        current: Dict[str, Any] = {}
        if path.exists():
            try:
                loaded = read_json(path)
                current = dict(_json_object(loaded))
            except (OSError, json.JSONDecodeError):
                current = {}
        changed = False
        for key, payload in seeded_entries.items():
            current_payload = current.get(key)
            if isinstance(current_payload, dict):
                if _line_text(current_payload.get('sessionId')) == _line_text(payload.get('sessionId')):
                    is_empty_default = _gateway_default_session_has_empty_transcript(path, current_payload)
                    needs_webchat_visibility = (
                        _line_text(current_payload.get('lastChannel')) != 'webchat'
                        or _line_text(_json_object(current_payload.get('deliveryContext')).get('channel')) != 'webchat'
                        or _line_text(_json_object(current_payload.get('origin')).get('provider')) != 'webchat'
                    )
                    for field, value in payload.items():
                        current_value = current_payload.get(field)
                        if (
                            field not in current_payload
                            or current_value in (None, '')
                            or (field in visibility_fields and needs_webchat_visibility)
                            or (field in seed_time_fields and current_value == 0)
                            or (field in seed_time_fields and is_empty_default and current_value != value)
                        ):
                            current_payload[field] = value
                            changed = True
                    current[key] = current_payload
                continue
            current[key] = payload
            changed = True
        if changed or not path.exists():
            write_text(path, json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    for path in gateway_default_session_transcript_targets(registry, resolver):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def render_gateway_agent_state_dirs(repo_root: Path, resolver: PathResolver, config_path: Path | None = None) -> None:
    registry = _load_registry(config_path or resolver.config_path)
    prune_stale_gateway_agent_state_dirs(registry, resolver)
    for path in gateway_agent_state_dir_targets(registry, resolver):
        path.mkdir(parents=True, exist_ok=True)
    for path, content in gateway_healthcheck_script_targets(repo_root, resolver).items():
        write_text(path, content)
    for path, content in gateway_router_workspace_file_targets(repo_root, resolver).items():
        write_text(path, content)
    for path, content in gateway_router_agent_file_targets(repo_root, resolver).items():
        write_text(path, content)
    for path, content in gateway_agent_core_file_targets(registry, resolver).items():
        write_text(path, content)
    render_gateway_default_sessions(registry, resolver)


def render_gateway_exec_approvals(resolver: PathResolver) -> None:
    paths = resolver.gateway_exec_approvals_paths()
    write_text(paths['host_output'], resolver.read_gateway_exec_approvals_source())
