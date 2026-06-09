"""Gateway openclaw.json 配置派生产物。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Dict, List

from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.runtime.path_resolver import PathResolver

from ..io import read_json, write_text
from ..registry import (
    _deploy_env_values,
    _is_unresolved_runtime_value,
    _load_registry,
    _registry_owned_index,
    _registry_rows,
    _runtime_value,
)
from ..shared import _json_object, _json_object_rows, _line_text, _positive_int, _string_rows, _text
from .constants import GATEWAY_INTERACTIVE_DEFAULTS, GATEWAY_UI_SKILL_GOVERNANCE_CONTRACT_ID
from .workspace import build_gateway_agent_projection


def _gateway_interactive_defaults() -> Dict[str, Any]:
    return deepcopy(GATEWAY_INTERACTIVE_DEFAULTS)


def _official_gateway_pin_version(repo_root: Path) -> str:
    pin_path = repo_contract_path('image_pins.openclaw', root_dir=repo_root)
    try:
        lines = pin_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return 'unknown'
    for line in lines:
        if not line.startswith('OPENCLAW_OFFICIAL_GATEWAY_IMAGE='):
            continue
        image = line.split('=', 1)[1].strip().split('@', 1)[0]
        if ':' not in image:
            break
        tag = image.rsplit(':', 1)[1].strip()
        return tag or 'unknown'
    return 'unknown'


def gateway_skill_governance_contract_path(repo_root: Path) -> Path:
    return repo_contract_path(GATEWAY_UI_SKILL_GOVERNANCE_CONTRACT_ID, root_dir=repo_root)


def load_gateway_skill_governance(repo_root: Path) -> Dict[str, Any]:
    payload = read_json(gateway_skill_governance_contract_path(repo_root))
    allowed_keys = {'version', 'targetOpenClawVersion', 'purpose', 'disabledSkills'}
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"Gateway skill governance 包含未声明字段：{', '.join(unknown_keys)}")
    if payload.get('version') != 1:
        raise ValueError('Gateway skill governance version 必须为 1')
    target_version = _line_text(payload.get('targetOpenClawVersion'))
    if not target_version:
        raise ValueError('Gateway skill governance targetOpenClawVersion 不能为空')
    raw_disabled_skills = payload.get('disabledSkills')
    if not isinstance(raw_disabled_skills, list):
        raise ValueError('Gateway skill governance disabledSkills 必须是列表')
    disabled_skills = _string_rows(raw_disabled_skills)
    if len(disabled_skills) != len(raw_disabled_skills):
        raise ValueError('Gateway skill governance disabledSkills 只能包含非空字符串')
    if not disabled_skills:
        raise ValueError('Gateway skill governance disabledSkills 不能为空')
    duplicate_skills = sorted({name for name in disabled_skills if disabled_skills.count(name) > 1})
    if duplicate_skills:
        raise ValueError(f"Gateway skill governance disabledSkills 包含重复项：{', '.join(duplicate_skills)}")
    normalized = deepcopy(payload)
    normalized['targetOpenClawVersion'] = target_version
    normalized['disabledSkills'] = disabled_skills
    return normalized


def ensure_gateway_config_meta(config: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    merged = deepcopy(config)
    meta = dict(_json_object(merged.get('meta')))
    meta.setdefault('lastTouchedVersion', _official_gateway_pin_version(repo_root))
    meta.setdefault('lastTouchedAt', '1970-01-01T00:00:00.000Z')
    merged['meta'] = meta
    return merged


def _used_model_refs(registry: Dict[str, Any]) -> List[str]:
    refs: set[str] = set()
    agents_by_ref = _registry_owned_index(registry, 'agents')
    for agent in _registry_rows(registry, 'agents'):
        model_ref = (
            _line_text(agent.get('resolvedDefaultModelProfileRef'))
            or _line_text(agent.get('defaultModelProfileRef'))
        )
        if model_ref:
            refs.add(model_ref)
    for job in _registry_rows(registry, 'jobs'):
        model_ref = (
            _line_text(job.get('resolvedModelProfileQualifiedRef'))
            or _line_text(job.get('resolvedModelProfileRef'))
            or _line_text(job.get('modelProfileRef'))
        )
        if not model_ref:
            agent_ref = _line_text(job.get('resolvedAgentQualifiedRef')) or _line_text(job.get('agentRef'))
            agent = agents_by_ref.get(agent_ref)
            model_ref = (
                _line_text((agent or {}).get('resolvedDefaultModelProfileRef'))
                or _line_text((agent or {}).get('defaultModelProfileRef'))
            )
        if model_ref:
            refs.add(model_ref)
    return sorted(refs)


def _model_remote_name(model: Dict[str, Any], env_values: Dict[str, str]) -> str:
    provider = _line_text(model.get('provider'))
    model_ref = _line_text(model.get('modelRef'))
    env_ref = _runtime_value(env_values, _line_text(model.get('modelRefEnv')))
    if env_ref:
        model_ref = f'{provider}/{env_ref}' if provider and '/' not in env_ref else env_ref
    prefix = f'{provider}/'
    if provider and model_ref.startswith(prefix):
        return _line_text(model_ref[len(prefix):])
    return model_ref


def _gateway_model_entry(model: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    capabilities = _json_object(model.get('capabilities'))
    cost_policy = _json_object(model.get('costPolicy'))
    token_rates = _json_object(cost_policy.get('tokenRates'))
    entry: Dict[str, Any] = {
        'id': model_id,
        'name': model_id,
        'reasoning': bool(capabilities.get('reasoning')),
        'input': ['text', 'image'] if bool(capabilities.get('vision')) else ['text'],
        'cost': {
            'input': float(token_rates.get('inputPerMillionTokens') or 0),
            'output': float(token_rates.get('outputPerMillionTokens') or 0),
            'cacheRead': float(token_rates.get('promptCacheReadPerMillionTokens') or 0),
            'cacheWrite': float(token_rates.get('promptCacheWritePerMillionTokens') or 0),
        },
        'contextWindow': _positive_int(capabilities.get('contextWindow'), default=32768),
        'maxTokens': _positive_int(capabilities.get('maxTokens'), default=4096),
    }
    return entry


_GATEWAY_OLLAMA_AUDIT_SANDBOX_MODE = 'all'
_GATEWAY_OLLAMA_AUDIT_TOOL_DENY = ('group:web', 'browser')


def build_gateway_model_projection(registry: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    env_values = _deploy_env_values(repo_root)
    models_by_ref = _registry_owned_index(registry, 'models')
    ollama_models: List[Dict[str, Any]] = []
    seen_model_ids: set[str] = set()
    base_url = ''
    for model_ref in _used_model_refs(registry):
        model = _json_object(models_by_ref.get(model_ref))
        provider = _line_text(model.get('provider'))
        channel = _json_object(model.get('channel'))
        if provider != 'ollama' or _line_text(channel.get('api')) not in {'ollama-chat', 'ollama'}:
            continue
        model_id = _model_remote_name(model, env_values)
        candidate_base_url = _runtime_value(env_values, _line_text(channel.get('baseUrlEnv')))
        if _is_unresolved_runtime_value(model_id) or _is_unresolved_runtime_value(candidate_base_url):
            continue
        base_url = base_url or candidate_base_url.rstrip('/')
        if model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)
        ollama_models.append(_gateway_model_entry(model, model_id))

    if not ollama_models or not base_url:
        return {}
    return {
        'defaultModel': f"ollama/{ollama_models[0]['id']}",
        'models': {
            'mode': 'merge',
            'providers': {
                'ollama': {
                    'baseUrl': base_url,
                    'apiKey': 'ollama-local',
                    'api': 'ollama',
                    'request': {'allowPrivateNetwork': True},
                    'models': ollama_models,
                },
            },
        },
        'agentModelAliases': {
            f"ollama/{row['id']}": {
                'alias': 'Local Ollama' if index == 0 else str(row.get('name') or row['id']),
            }
            for index, row in enumerate(ollama_models)
        },
        'audit': {
            'agents': {'defaults': {'sandbox': {'mode': _GATEWAY_OLLAMA_AUDIT_SANDBOX_MODE}}},
            'tools': {'deny': list(_GATEWAY_OLLAMA_AUDIT_TOOL_DENY)},
        },
    }


def build_gateway_model_runtime_env_lines(registry: Dict[str, Any], repo_root: Path) -> List[str]:
    projection = build_gateway_model_projection(registry, repo_root)
    provider = _json_object(_json_object(_json_object(projection.get('models')).get('providers')).get('ollama'))
    if not provider:
        return []
    base_url = _line_text(provider.get('baseUrl')).rstrip('/')
    lines = [
        '',
        '# 由 active control-plane model registry 派生；启用 Gateway UI 本地 Ollama 聊天可用性判定。',
        'OLLAMA_API_KEY=ollama-local',
    ]
    if base_url:
        lines.append(f'OLLAMA_BASE_URL={base_url}')
    return lines


def merge_gateway_model_projection(config: Dict[str, Any], projection: Dict[str, Any]) -> Dict[str, Any]:
    if not projection:
        return config
    merged = deepcopy(config)
    models_section = dict(_json_object(merged.get('models')))
    projected_models = _json_object(projection.get('models'))
    if _line_text(projected_models.get('mode')) and not _line_text(models_section.get('mode')):
        models_section['mode'] = _line_text(projected_models.get('mode'))
    providers = dict(_json_object(models_section.get('providers')))
    for provider_id, provider_payload in _json_object(projected_models.get('providers')).items():
        provider_key = _line_text(provider_id)
        if not provider_key:
            continue
        current_provider = dict(_json_object(providers.get(provider_key)))
        incoming_provider = _json_object(provider_payload)
        current_models = _json_object_rows(current_provider.get('models'))
        existing_model_ids = {_line_text(row.get('id')) for row in current_models if _line_text(row.get('id'))}
        incoming_models = [
            deepcopy(row)
            for row in _json_object_rows(incoming_provider.get('models'))
            if _line_text(row.get('id')) and _line_text(row.get('id')) not in existing_model_ids
        ]
        merged_provider = {**incoming_provider, **current_provider}
        merged_provider['models'] = current_models + incoming_models
        providers[provider_key] = merged_provider
    models_section['providers'] = providers
    merged['models'] = models_section

    agents_section = dict(_json_object(merged.get('agents')))
    defaults = dict(_json_object(agents_section.get('defaults')))
    for key, value in _gateway_interactive_defaults().items():
        defaults.setdefault(key, value)
    projected_audit = _json_object(projection.get('audit'))
    projected_agent_defaults = _json_object(_json_object(projected_audit.get('agents')).get('defaults'))
    projected_sandbox = _json_object(projected_agent_defaults.get('sandbox'))
    sandbox_mode = _line_text(projected_sandbox.get('mode'))
    if sandbox_mode:
        sandbox_defaults = dict(_json_object(defaults.get('sandbox')))
        sandbox_defaults['mode'] = sandbox_mode
        defaults['sandbox'] = sandbox_defaults
    model_defaults = dict(_json_object(defaults.get('model')))
    default_model = _line_text(projection.get('defaultModel'))
    if default_model and not _line_text(model_defaults.get('primary')):
        model_defaults['primary'] = default_model
    defaults['model'] = model_defaults
    configured_models = dict(_json_object(defaults.get('models')))
    for model_key, alias_payload in _json_object(projection.get('agentModelAliases')).items():
        if _line_text(model_key) and _line_text(model_key) not in configured_models:
            configured_models[_line_text(model_key)] = deepcopy(alias_payload)
    if configured_models:
        defaults['models'] = configured_models
    agents_section['defaults'] = defaults
    merged['agents'] = agents_section
    projected_tools = _json_object(projected_audit.get('tools'))
    projected_tools_deny = _string_rows(projected_tools.get('deny'))
    if projected_tools_deny:
        tools_section = dict(_json_object(merged.get('tools')))
        tools_deny = _string_rows(tools_section.get('deny'))
        seen_tools = set(tools_deny)
        for item in projected_tools_deny:
            if item in seen_tools:
                continue
            tools_deny.append(item)
            seen_tools.add(item)
        tools_section['deny'] = tools_deny
        merged['tools'] = tools_section
    return merged


def merge_gateway_skill_governance(config: Dict[str, Any], governance: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(config)
    skills_section = dict(_json_object(merged.get('skills')))
    skills_section.pop('allowBundled', None)
    entries = dict(_json_object(skills_section.get('entries')))
    for skill_name in _string_rows(governance.get('disabledSkills')):
        entry = dict(_json_object(entries.get(skill_name)))
        entry['enabled'] = False
        entries[skill_name] = entry
    skills_section['entries'] = entries
    merged['skills'] = skills_section
    return merged


def merge_gateway_agent_projection(config: Dict[str, Any], projection: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not projection:
        return config
    merged = deepcopy(config)
    agents_section = dict(_json_object(merged.get('agents')))
    defaults = dict(_json_object(agents_section.get('defaults')))
    for key, value in _gateway_interactive_defaults().items():
        defaults.setdefault(key, value)
    agents_section['defaults'] = defaults
    existing = _json_object_rows(agents_section.get('list'))
    existing_ids = {_text(agent.get('id')) for agent in existing if _text(agent.get('id'))}
    existing_has_default = any(bool(agent.get('default')) for agent in existing)
    projected = []
    for agent in projection:
        if _text(agent.get('id')) in existing_ids:
            continue
        row = deepcopy(agent)
        if existing_has_default:
            row.pop('default', None)
        projected.append(row)
    rows = [deepcopy(agent) for agent in existing] + projected
    if rows and not any(bool(row.get('default')) for row in rows):
        rows[0]['default'] = True
    agents_section['list'] = rows
    merged['agents'] = agents_section
    return merged


def public_openclaw_source_path(repo_root: Path) -> Path:
    return repo_root / 'config' / 'gateway' / 'openclaw.gateway.json'


def public_openclaw_state_path(resolver: PathResolver) -> Path:
    return resolver.absolute_host_path('openclaw_config')


def build_public_openclaw_config_output(repo_root: Path, resolver: PathResolver, config_path: Path | None = None) -> str:
    config = read_json(public_openclaw_source_path(repo_root))
    registry = _load_registry(config_path or resolver.config_path)
    config = merge_gateway_agent_projection(config, build_gateway_agent_projection(registry, resolver))
    config = merge_gateway_model_projection(config, build_gateway_model_projection(registry, repo_root))
    config = merge_gateway_skill_governance(config, load_gateway_skill_governance(repo_root))
    config = ensure_gateway_config_meta(config, repo_root)
    return json.dumps(config, ensure_ascii=False, indent=2) + '\n'


def render_public_openclaw_config(repo_root: Path, resolver: PathResolver, config_path: Path | None = None) -> None:
    write_text(public_openclaw_state_path(resolver), build_public_openclaw_config_output(repo_root, resolver, config_path))
