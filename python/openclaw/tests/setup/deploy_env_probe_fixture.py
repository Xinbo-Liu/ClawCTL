"""构造部署输入回归使用的受管扩展、变量声明与组合 profile。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.doctor.agent_modules.managed_probe_fixture import (
    ManagedProbeExtensionFixture,
    materialize_managed_probe_extension,
)
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import copy_tree_files_if_missing, write_json


@dataclass(frozen=True)
class DeployEnvProbeProfiles:
    """保存部署回归中单扩展与双扩展组合的真实配置入口。"""

    primary: ManagedProbeExtensionFixture
    peer: ManagedProbeExtensionFixture
    combination_id: str
    combination_path: Path


def _env_field(key: str, group: str, location: str, default: str, **attributes: Any) -> dict[str, Any]:
    """创建归属明确、具有可渲染默认值的部署变量声明。

    参数：key（str）为变量名，group（str）为文档分组，location（str）为输入位置，
        default（str）为默认值；attributes（Any）覆盖必填规则等声明属性。
    返回：dict[str, Any]，包含部署 schema 所需的基础属性与指定约束。
    副作用：无；只创建独立字典。
    """
    return {
        'key': key,
        'group': group,
        'required': False,
        'manual_required': False,
        'default_kind': 'literal',
        'default': default,
        'doc_summary': f'部署输入探针变量 {key}',
        'doc_location': location,
        'validator': {'type': 'non_empty'},
        **attributes,
    }


def materialize_deploy_env_probe_profiles(repo_root: Path, *, base_repo_root: Path) -> DeployEnvProbeProfiles:
    """在受管探针仓库注册独立变量、共享模型变量与组合配置。

    参数：repo_root（Path）为测试拥有的临时仓库；base_repo_root（Path）提供基座合同与 schema。
    返回：DeployEnvProbeProfiles，提供两个受管 owner 和组合 profile 的文件入口。
    副作用：调用现有 materializer 写入探针，补写真实 manifest、schema、profile registry 与 env 示例。
    异常：文件读写或 JSON 解析失败直接传播，测试不得以缺失 fixture 为由跳过。
    """
    repo_root = Path(repo_root).resolve()
    primary = materialize_managed_probe_extension(repo_root, base_repo_root=base_repo_root)
    peer = materialize_managed_probe_extension(repo_root, base_repo_root=base_repo_root, extension_id='agent_probe_peer')
    copy_tree_files_if_missing(repo_root / 'config' / 'image_pins', base_repo_root / 'config' / 'image_pins')
    for fixture in (primary, peer):
        manifest = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
        if fixture is peer:
            # 第二个 owner 仅贡献部署 schema，不重复注册探针的运行对象和全局标识。
            manifest['registry'] = {}
            manifest['surfaceFragments'] = {}
            manifest['governanceSurfaces'] = {}
        schema_name = 'probe.deploy_env_schema.json'
        manifest.setdefault('surfaceFragments', {})['deployEnvSchemaPath'] = schema_name
        write_json(fixture.manifest_path, manifest)
        location = f'agent/extensions/{fixture.extension_id}/deploy/extension.env'
        fields = [
            _env_field('OLLAMA_BASE_URL', 'model_providers', location, 'http://fixture-ollama.invalid:11434',
                       required=True, manual_required=True, validator={'type': 'http_url'}),
            _env_field('OLLAMA_MODEL_REF', 'model_providers', location, 'fixture_model',
                       required=True, manual_required=True),
        ]
        groups = [{'id': 'model_providers', 'title': '共享模型输入'}]
        if fixture is primary:
            group_id = 'probe_provider'
            groups.append({'id': group_id, 'title': '探针扩展输入'})
            fields.append(_env_field('PROBE_PROVIDER_WEBHOOK_URL', group_id, location, ''))
            for key in (
                'PROBE_NOTIFY_APP_NAME', 'PROBE_NOTIFY_APP_ID', 'PROBE_NOTIFY_CHANNEL_ID',
                'PROBE_NOTIFY_ACTOR_ID', 'PROBE_NOTIFY_BOT_NAME', 'PROBE_ADMIN_USERS_JSON',
            ):
                fields.append(_env_field(key, group_id, location, ''))
            fields.append(_env_field('PROBE_NOTIFY_LIVE_REQUIRED', group_id, location, '0'))
            for key in ('PROBE_NOTIFY_CARD_ENABLED', 'PROBE_NOTIFY_CARD_UPDATE_ENABLED'):
                fields.append(_env_field(
                    key, group_id, location, '1', truthy_required=True,
                    conditional_required={'when': {'key': 'PROBE_NOTIFY_LIVE_REQUIRED', 'truthy': True}},
                ))
            fields.append(_env_field('PROBE_NOTIFY_CARD_ACTION_TRIGGER_ENABLED', group_id, location, '1'))
        else:
            groups.append({'id': 'probe_peer', 'title': '第二个探针输入'})
            fields.append(_env_field('PROBE_PEER_SETTING', 'probe_peer', location, 'peer_default'))
        write_json(fixture.manifest_dir / schema_name, {'schema_version': 1, 'groups': groups, 'fields': fields})
        example = fixture.package_root / 'deploy' / 'extension.env.example'
        example.parent.mkdir(parents=True, exist_ok=True)
        example.write_text(''.join(f"{field['key']}={field['default']}\n" for field in fields), encoding='utf-8')

    # 本场景验证部署变量归属；模型声明使用固定本地命令，避免增加无关 API 凭据前置条件。
    for model_path in primary.models_dir.glob('*.json'):
        model = json.loads(model_path.read_text(encoding='utf-8'))
        model['provider'] = 'fixture_local'
        model['channel'] = {'kind': 'local_process', 'api': 'local-process-json', 'localProcess': {'command': ['fixture-model']}}
        model['modelRef'] = 'fixture/local-model'
        model.pop('modelRefEnv', None)
        write_json(model_path, model)
    registry_path = primary.package_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    registry['targets'][0]['enabledDefault'] = True
    registry['targets'][0]['lifecycleState'] = 'active'
    write_json(registry_path, registry)

    combination_id = 'agent_probe_combination'
    combination_rel_path = f'config/control_plane/profiles/{combination_id}.service.json'
    combination_path = repo_root / combination_rel_path
    manifest_dirs = [
        '@repo/config/control_plane/extensions.d',
        *[f'@repo/{fixture.manifest_dir.relative_to(repo_root).as_posix()}' for fixture in (primary, peer)],
    ]
    enabled_ids = ['agent_platform', primary.extension_id, peer.extension_id]
    write_json(combination_path, {
        'extends': '@repo/config/control_plane/service.json',
        'extensions': {'manifestsDirs': manifest_dirs, 'enabledExtensionIds': enabled_ids},
    })
    write_json(repo_root / 'config' / 'control_plane' / 'repo_combination_profiles.json', {'profiles': [{
        'id': combination_id,
        'configPath': combination_rel_path,
        'enabledExtensionIds': enabled_ids,
        'manifestsDirs': manifest_dirs,
        'sharedDeployEnvFields': [{
            'keys': ['OLLAMA_BASE_URL', 'OLLAMA_MODEL_REF'],
            'extensionIds': [primary.extension_id, peer.extension_id],
        }],
    }]})
    profile_registry = repo_root / 'config' / 'control_plane' / 'profile_registry.tsv'
    with profile_registry.open('a', encoding='utf-8') as handle:
        for fixture in (primary, peer):
            handle.write(f'{fixture.extension_id}\t{fixture.service_path.relative_to(repo_root).as_posix()}\n')
        handle.write(f'{combination_id}\t{combination_rel_path}\n')
    return DeployEnvProbeProfiles(primary, peer, combination_id, combination_path)
