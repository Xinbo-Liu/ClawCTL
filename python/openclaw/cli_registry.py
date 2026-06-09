#!/usr/bin/env python3
"""OpenClaw 顶层 CLI 命令树注册表。"""
from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from openclaw.control_plane.extensions.api import extension_cli_commands
from openclaw.lib.repo.layout import resolve_selected_runtime_control_plane_service_config_path


CommandTarget: TypeAlias = str
CommandNode: TypeAlias = dict[str, 'CommandTreeValue']
CommandTreeValue: TypeAlias = CommandTarget | CommandNode


# 命令名属于公共 CLI 协议，不得随说明文字调整；本注册表维护命令到正式模块入口的路由。
ROOT_COMMAND_TREE: CommandNode = {
    'setup': {
        'env': 'openclaw.setup.deploy_env.control_plane',
        'ingress': 'openclaw.setup.network.gateway_ingress',
        'upgrade': 'openclaw.setup.upgrade.main',
        'surface': {
            'entrypoints': 'openclaw.setup.surface.entrypoint',
            'followups': 'openclaw.setup.surface.followup',
            'failures': 'openclaw.setup.surface.failure',
        },
        'flow': {
            'deploy': 'openclaw.setup.flow.deploy_flow',
            'deploy-success': 'openclaw.setup.flow.deploy_success',
            'deploy-failure': 'openclaw.setup.flow.run_failure',
            'one-click-deploy': 'openclaw.setup.flow.one_click_deploy',
            'one-click-test': 'openclaw.setup.flow.one_click_test',
            'full-test-surface': 'openclaw.lib.testing.full_test',
            'config-summary': 'openclaw.setup.flow.config_summary',
            'basic-summary': 'openclaw.setup.flow.basic_summary',
        },
    },
    'runtime': {
        'paths': 'openclaw.runtime.path_surface',
        'workspace': 'openclaw.runtime.workspace_templates',
        'surface': 'openclaw.lib.runtime.surface',
        'healthcheck': 'openclaw.runtime.healthcheck',
        'mounts': 'openclaw.runtime.compose_mount_registry',
        'acceptance': 'openclaw.lib.testing.acceptance_surface',
        'release': 'openclaw.release.bundle_governance',
    },
    'dispatch': {
        'ops': 'openclaw.lib.dispatch.operations_surface',
        'observability': 'openclaw.lib.dispatch.observability_surface',
    },
    'control-plane': {
        'config': 'openclaw.lib.repo.control_plane_config_surface',
        'objects': 'openclaw.lib.control_plane.object_families',
        'artifacts': 'openclaw.control_plane.artifact_policies',
        'diagnostics': 'openclaw.lib.control_plane.diagnostic_surface',
        'agent-cli': 'openclaw.lib.control_plane.agent_cli_surface',
        'recovery': 'openclaw.lib.control_plane.recovery_operations_surface',
        'routes': 'openclaw.lib.control_plane.router_route_surface',
        'scheduler-runtime': 'openclaw.scheduler.runtime',
        'api': {
            'internal-runtime': 'openclaw.internal_api.app',
        },
        'scripts': 'openclaw.lib.control_plane.script_catalog_surface',
        'validate': 'openclaw.control_plane.cli:validate_entry',
        'summary': 'openclaw.control_plane.cli:summary_entry',
        'module': 'openclaw.control_plane.cli:module_entry',
        'evidence': 'openclaw.control_plane.cli:evidence_entry',
        'runtime': 'openclaw.control_plane.cli:runtime_entry',
        'extension': 'openclaw.control_plane.cli:extension_entry',
        'extensions': 'openclaw.control_plane.extension_lifecycle',
        'stack': 'openclaw.control_plane.stack.release',
        'facts': {
            'overview': 'openclaw.control_plane.facts:overview_entry',
        },
    },
    'docs': {
        'deployment-inputs': 'openclaw.setup.deploy_env.control_plane:docs_entry',
        'render-getting-started': 'openclaw.docs.renderers.getting_started:render_entry',
        'render-maintenance-map': 'openclaw.docs.renderers.maintenance_map:render_entry',
        'render-runtime-surface': 'openclaw.docs.renderers.runtime_surface:render_entry',
    },
    'images': {
        'check-overlay-contract': 'openclaw.images.upstream_overlay_contract',
        'governance-surface': 'openclaw.images.governance_surface',
    },
    'guards': {
        'host-python-doc': 'openclaw.guards.host_python_doc_guard',
        'host-python-shell': 'openclaw.guards.host_python_shell_guard',
        'keyword-gate-inventory': 'openclaw.doctor.platform.keyword_gate_inventory',
    },
}


CONTROL_PLANE_GROUP_NAMESPACES = ('validate', 'summary', 'module', 'evidence', 'runtime')


def active_config_path(config_path: Path | None = None) -> Path:
    return resolve_selected_runtime_control_plane_service_config_path(
        config_path,
        start_path=Path(__file__),
        default_to_runtime=True,
    )


def control_plane_extension_commands(config_path: Path | None = None) -> dict[str, str]:
    return extension_cli_commands(active_config_path(config_path))


def control_plane_command_tree(config_path: Path | None = None) -> CommandNode:
    subtree = dict(ROOT_COMMAND_TREE['control-plane'])  # shallow copy
    subtree['api'] = dict(subtree['api'])  # type: ignore[arg-type]
    return subtree


def root_command_tree(config_path: Path | None = None) -> CommandNode:
    tree = dict(ROOT_COMMAND_TREE)
    tree['setup'] = dict(tree['setup'])  # type: ignore[arg-type]
    tree['runtime'] = dict(tree['runtime'])  # type: ignore[arg-type]
    tree['dispatch'] = dict(tree['dispatch'])  # type: ignore[arg-type]
    tree['docs'] = dict(tree['docs'])  # type: ignore[arg-type]
    tree['images'] = dict(tree['images'])  # type: ignore[arg-type]
    tree['guards'] = dict(tree['guards'])  # type: ignore[arg-type]
    tree['control-plane'] = control_plane_command_tree(config_path)
    return tree


def command_node(tree: CommandNode, *tokens: str) -> CommandTreeValue | None:
    current: CommandTreeValue = tree
    for token in tokens:
        if not isinstance(current, dict):
            return None
        current = current.get(token)
        if current is None:
            return None
    return current


def supported_root_commands(config_path: Path | None = None) -> list[str]:
    return sorted(root_command_tree(config_path).keys())


def supported_control_plane_group_namespaces() -> list[str]:
    return list(CONTROL_PLANE_GROUP_NAMESPACES)
