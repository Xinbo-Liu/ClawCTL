#!/usr/bin/env python3
"""受管 agent 模块 scaffold 的共享模板与合同辅助。"""
from __future__ import annotations

from typing import Any, Sequence

from openclaw.lib.repo.layout import CONTROL_PLANE_PROFILE_CONFIG_SUFFIX, CONTROL_PLANE_PROFILES_REL_DIR
from openclaw.lib.repo.path_contracts import repo_anchored_path


DEFAULT_CHANGE_CONTROL_DOC_TARGETS = (
    repo_anchored_path('docs/architecture/agent-governance.md'),
    repo_anchored_path('docs/architecture/agent-module-governance.md'),
)

OPTIONAL_SURFACE_MARKERS = {
    # 这些标记只用于 prune/optional-surface 检测；不要把模板壳当成业务真源。
    'agents': '<!-- openclaw:boilerplate-surface=agents -->',
    'contracts': '<!-- openclaw:boilerplate-surface=contracts -->',
    'constraints': '<!-- openclaw:boilerplate-surface=constraints -->',
    'docs': '<!-- openclaw:boilerplate-surface=docs -->',
    'tests': '<!-- openclaw:boilerplate-surface=tests -->',
}


def managed_extension_profile_rel_path(extension_id: str) -> str:
    normalized = str(extension_id or '').strip()
    if not normalized:
        raise ValueError('managed extension id 不能为空')
    return f'{CONTROL_PLANE_PROFILES_REL_DIR}/{normalized}{CONTROL_PLANE_PROFILE_CONFIG_SUFFIX}'


def boilerplate_surface_marker(surface: str) -> str:
    return OPTIONAL_SURFACE_MARKERS[str(surface or '').strip()]


def has_boilerplate_surface_marker(text: str, *, surface: str) -> bool:
    return boilerplate_surface_marker(surface) in str(text or '')


def normalize_boilerplate_text(text: str) -> str:
    normalized_lines = [line.rstrip() for line in str(text or '').replace('\r\n', '\n').strip().split('\n')]
    return '\n'.join(normalized_lines) + '\n'

def build_module_assets(module_ref: str, *, with_agents_doc: bool = False) -> dict[str, Any]:
    assets: dict[str, Any] = {
        'readmePath': 'README.md',
        'binPath': f'bin/{module_ref}',
        'skillsPath': 'skills.md',
        'permissionsPath': 'permissions.json',
        'toolsPath': 'tools.json',
    }
    if with_agents_doc:
        assets['agentsMdPath'] = 'AGENTS.md'
    return assets


def build_module_payload(
    *,
    module_ref: str,
    title: str,
    owner_domain: str,
    entrypoint_kind: str,
    runtime_adapter_ref: str,
    implementation_ref: str,
    logic_source_paths: Sequence[str],
    activation_extension_ids: Sequence[str],
    change_control_doc_paths: Sequence[str],
    operations: dict[str, Any],
    contract: dict[str, Any],
    control_plane_agent: dict[str, Any],
    control_plane_implementation: dict[str, Any],
    with_agents_doc: bool = False,
    module_kind: str = 'worker',
    version: str = 'v1',
    skill_set_ref: str | None = None,
    permission_policy_ref: str | None = None,
    toolset_ref: str | None = None,
) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'id': module_ref,
        'agentRef': module_ref,
        'title': title,
        'version': version,
        'activation': {
            'enabledExtensionIds': [str(item).strip() for item in activation_extension_ids if str(item).strip()],
        },
        'ownerDomain': owner_domain,
        'moduleKind': module_kind,
        'runtime': {
            'entrypointKinds': [entrypoint_kind],
            'runtimeAdapterRefs': [runtime_adapter_ref],
        },
        'logic': {
            'implementationRef': implementation_ref,
            'sourcePaths': [str(item).strip() for item in logic_source_paths if str(item).strip()],
        },
        'assets': build_module_assets(module_ref, with_agents_doc=with_agents_doc),
        'governance': {
            'changeControlDocPaths': [str(item).strip() for item in change_control_doc_paths if str(item).strip()],
        },
        'assembly': {
            'skillSetRef': skill_set_ref or module_ref,
            'permissionPolicyRef': permission_policy_ref or module_ref,
            'toolsetRef': toolset_ref or module_ref,
        },
        'operations': operations,
        'contract': contract,
        'controlPlane': {
            'agent': dict(control_plane_agent),
            'implementation': dict(control_plane_implementation),
        },
    }


def module_readme_template(
    module_ref: str,
    title: str,
    owner_domain: str,
    module_kind: str,
    entrypoint_kind: str,
    runtime_adapter_ref: str,
    operation_ref: str,
    *,
    module_dir_display: str,
    module_manifest_display: str,
    implementation_source_display: str,
    launcher_display: str,
    group_display: str,
    shared_objects_display: str,
) -> str:
    return f'''# {title}

## 角色

`{module_ref}` 是 `{owner_domain}` 域下的 Agent 模块。仓库内 Agent 模块固定放在受管显式扩展包内。

## 状态

- 模块类型：`{module_kind}`
- 运行入口：`{entrypoint_kind}`
- runtime adapter：`{runtime_adapter_ref}`
- 默认 operation：`{operation_ref}`
- 调度绑定：默认不预置 job 绑定；按模块实际运行面在 `module.json -> operations.*.jobBindings` 中登记。

## 真源路径

| 对象 | 路径 |
| --- | --- |
| 模块目录 | `{module_dir_display}` |
| 模块清单 | `{module_manifest_display}` |
| 实现真源 | `{implementation_source_display}` |
| 启动器 | `{launcher_display}` |
| group 注册面 | `{group_display}` |
| 共享对象目录 | `{shared_objects_display}` |

## 模块交付检查点

1. `skills.md`、`permissions.json`、`tools.json` 必须填写模块实际内容。
2. 模块运行实现与输入输出 contract 必须保持一致。
3. `module.json -> operations.*.jobBindings` 必须登记模块实际调度绑定。
4. 模块进入 group 时，只修改扩展包内的 group 注册面，不要回落到仓库根级目录。
'''


def agents_md_template(module_ref: str, *, launcher_display_path: str) -> str:
    return f'''# {module_ref} 协作说明

{boilerplate_surface_marker('agents')}

- 统一通过 control-plane 注册入口或 `{launcher_display_path}` 调用。
- 变更运行逻辑、skills、permissions、tools 时，同步更新 `module.json` 与真实实现，不引入第二份真源。
'''


def skills_template(module_ref: str, title: str, *, bullets: Sequence[str] | None = None) -> str:
    rows = [f'# {title} skills', '']
    items = list(bullets) if bullets is not None else [f'- `{module_ref}_run`: 执行 {title} 的默认运行命令。']
    rows.extend(items)
    rows.append('')
    return '\n'.join(rows)


def contracts_readme_template(module_ref: str) -> str:
    return f'''# {module_ref} contracts

{boilerplate_surface_marker('contracts')}

当前目录为可选合同补充位。
'''


def constraints_readme_template(module_ref: str) -> str:
    return f'''# {module_ref} constraints

{boilerplate_surface_marker('constraints')}

当前目录为可选约束补充位。
'''


def docs_readme_template(module_ref: str) -> str:
    return f'''# {module_ref} docs

{boilerplate_surface_marker('docs')}

当前目录为可选局部说明页。
'''


def tests_readme_template(module_ref: str) -> str:
    return f'''# {module_ref} tests

{boilerplate_surface_marker('tests')}

本目录承载 smoke / regression 测试。
'''


tests_readme_template.__test__ = False


def optional_surface_template_variants(
    module_ref: str,
    *,
    surface: str,
    launcher_display_path: str = '',
) -> tuple[str, ...]:
    normalized_surface = str(surface or '').strip()
    if normalized_surface == 'agents':
        templates = (agents_md_template(module_ref, launcher_display_path=launcher_display_path),)
    elif normalized_surface == 'contracts':
        templates = (contracts_readme_template(module_ref),)
    elif normalized_surface == 'constraints':
        templates = (constraints_readme_template(module_ref),)
    elif normalized_surface == 'docs':
        templates = (docs_readme_template(module_ref),)
    elif normalized_surface == 'tests':
        templates = (tests_readme_template(module_ref),)
    else:
        raise ValueError(f'unknown optional surface: {normalized_surface or "<empty>"}')
    return tuple(dict.fromkeys(normalize_boilerplate_text(item) for item in templates))


def managed_extension_launcher_template(module_ref: str, extension_id: str) -> str:
    return f'''#!/usr/bin/env bash
# 用途：{module_ref} 启动器；统一通过主仓库 agent runtime 入口运行，由共享入口解析配置选择。
set -euo pipefail
MODULE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
ROOT_DIR="$MODULE_DIR"
while [[ "$ROOT_DIR" != "/" ]]; do
  if [[ -d "$ROOT_DIR/python/openclaw" && -f "$ROOT_DIR/config/control_plane/service.json" && -f "$ROOT_DIR/scripts/agent_runtime/run_agent_entrypoint.sh" ]]; then
    break
  fi
  ROOT_DIR="$(dirname "$ROOT_DIR")"
done
if [[ ! -d "$ROOT_DIR/python/openclaw" || ! -f "$ROOT_DIR/config/control_plane/service.json" || ! -f "$ROOT_DIR/scripts/agent_runtime/run_agent_entrypoint.sh" ]]; then
  echo "[{module_ref}][FAIL] 无法从模块目录解析仓库根：$MODULE_DIR" >&2
  exit 2
fi
exec bash "$ROOT_DIR/scripts/agent_runtime/run_agent_entrypoint.sh" {extension_id}:{module_ref} "$@"
'''
