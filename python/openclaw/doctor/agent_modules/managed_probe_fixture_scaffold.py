#!/usr/bin/env python3
"""Extension-scaffold helpers for the managed probe fixture."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openclaw.control_plane.manifest_fields import (
    GOVERNANCE_SURFACES_FIELD,
    SURFACE_FRAGMENTS_FIELD,
)
from openclaw.control_plane.modules.scaffold_support import (
    build_module_payload,
    managed_extension_launcher_template,
    managed_extension_profile_rel_path,
    module_readme_template,
    skills_template,
)
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import read_json, write_json, write_text
from openclaw.lib.repo.layout import (
    CONTROL_PLANE_EXTENSIONS_DIR_REL_PATH,
    CONTROL_PLANE_SCHEMAS_REL_DIR,
    CONTROL_PLANE_SERVICE_CONFIG_REL_PATH,
)
from openclaw.lib.repo.path_contracts import extension_anchored_path, repo_anchored_path


PROBE_EXTENSION_ID = 'agent_probe'
PROBE_PACKAGE_NAME = 'openclaw_ext_probe'
PROBE_OWNER_DOMAIN = 'probe'
PROBE_PRIMARY_MODULE_REF = 'probe_dispatcher'
PROBE_SUPPORT_MODULE_REF = 'probe_helper'
PROBE_GROUP_REF = 'probe_pipeline'
PROBE_JOB_REF = 'probe_dispatch_weekday'
PROBE_MODEL_REF = 'probe_model_default'
PROBE_TARGET_REF = 'dispatch_target_default'
PROBE_RUNTIME_ENTRY_ID = 'probe_dispatch_out_dir'
PROBE_DIAGNOSTIC_ACTION = 'run_probe'
PROBE_TEST_GROUP_ID = 'probe_pipeline'
PROBE_CHECK_ID = 'probe_chain_contract'
PROBE_RELEASE_CHECK_ID = 'agent_module_smoke_tests_agent_probe'
PROBE_CHANGE_CONTROL_DOC_PATHS = (
    repo_anchored_path('docs/architecture/agent-governance.md'),
    repo_anchored_path('docs/architecture/agent-module-governance.md'),
)


def module_readme(extension_id: str, module_ref: str) -> str:
    module_dir_rel = f'agent/extensions/{extension_id}/agent/modules/{module_ref}/'
    module_manifest_rel = f'{module_dir_rel}module.json'
    module_main_rel = f'agent/extensions/{extension_id}/python/{PROBE_PACKAGE_NAME}/modules/{module_ref}/main.py'
    group_rel = f'agent/extensions/{extension_id}/agent/control_plane/groups/{PROBE_GROUP_REF}.json'
    shared_rel = f'agent/extensions/{extension_id}/python/{PROBE_PACKAGE_NAME}/domains/{PROBE_OWNER_DOMAIN}/shared/'
    title = 'Probe Dispatcher' if module_ref == PROBE_PRIMARY_MODULE_REF else 'Probe Helper'
    operation_ref = 'send_default' if module_ref == PROBE_PRIMARY_MODULE_REF else 'inspect_default'
    entrypoint_kind = 'delivery_adapter' if module_ref == PROBE_PRIMARY_MODULE_REF else 'python_cli'
    return module_readme_template(
        module_ref,
        title,
        PROBE_OWNER_DOMAIN,
        'worker',
        entrypoint_kind,
        'python_module',
        operation_ref,
        module_dir_display=module_dir_rel,
        module_manifest_display=module_manifest_rel,
        implementation_source_display=module_main_rel,
        launcher_display=f'{module_dir_rel}bin/{module_ref}',
        group_display=group_rel,
        shared_objects_display=shared_rel,
    )


def module_skills(module_ref: str) -> str:
    title = 'Probe Dispatcher' if module_ref == PROBE_PRIMARY_MODULE_REF else 'Probe Helper'
    return skills_template(
        module_ref,
        title,
        bullets=[f'- `{module_ref}_smoke`: probe-only regression helper.'],
    )


def module_permissions(module_ref: str) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'moduleRef': module_ref,
        'allow': ['network_probe_whitelist'],
        'deny': ['unregistered_dispatch'],
    }


def module_tools(module_ref: str) -> dict[str, Any]:
    return {
        'schemaVersion': 1,
        'moduleRef': module_ref,
        'allowedTools': ['run_agent_entrypoint'],
        'forbiddenTools': ['unregistered_dispatch_adapter'],
        'auditFields': ['module_ref', 'run_id'],
    }


def module_launcher(module_ref: str, extension_id: str) -> str:
    return managed_extension_launcher_template(module_ref, extension_id)


def module_main(module_ref: str, description: str) -> str:
    return '\n'.join([
        'from __future__ import annotations',
        '',
        'import argparse',
        '',
        '',
        'def build_parser() -> argparse.ArgumentParser:',
        f"    parser = argparse.ArgumentParser(prog='{module_ref}', description='{description}')",
        "    parser.add_argument('command', nargs='?', default='run')",
        '    return parser',
        '',
        '',
        'def main(argv: list[str] | None = None) -> int:',
        '    parser = build_parser()',
        '    parser.parse_args(argv)',
        '    return 0',
        '',
        '',
        "if __name__ == '__main__':",
        '    raise SystemExit(main())',
        '',
    ])


def module_payload(
    extension_id: str,
    *,
    module_ref: str,
    title: str,
    implementation_ref: str,
    entrypoint_kind: str,
    external_dispatch: bool,
    runtime_module: str,
    source_paths: list[str],
    operations: dict[str, Any],
    filesystem_write: list[str],
) -> dict[str, Any]:
    return build_module_payload(
        module_ref=module_ref,
        title=title,
        owner_domain=PROBE_OWNER_DOMAIN,
        entrypoint_kind=entrypoint_kind,
        runtime_adapter_ref='python_module',
        implementation_ref=implementation_ref,
        logic_source_paths=source_paths,
        activation_extension_ids=[extension_id],
        change_control_doc_paths=PROBE_CHANGE_CONTROL_DOC_PATHS,
        operations=operations,
        contract={
            'inputs': {
                'artifacts': [],
                'runtimeInputs': [],
            },
            'outputs': {
                'artifacts': ['probe_dispatch_report_json'],
                'statusSignals': ['probe_dispatch_ready'],
            },
        },
        control_plane_agent={
            'title': f'{title} Agent',
            'entrypointKind': entrypoint_kind,
            'description': f'{title} fixture agent.',
            'capabilities': {
                'network': False,
                'filesystemWrite': filesystem_write,
                'modelRequired': False,
                'externalDispatch': external_dispatch,
            },
            'defaultModelProfileRef': PROBE_MODEL_REF,
        },
        control_plane_implementation={
            'title': f'{title} Implementation',
            'runtime': {
                'adapterRef': 'python_module',
                'config': {
                    'module': runtime_module,
                },
            },
        },
    )


def extension_row(extension_id: str) -> dict[str, Any]:
    profile_rel_path = managed_extension_profile_rel_path(extension_id)
    return {
        'id': extension_id,
        'title': 'Managed Probe Extension',
        'rootDir': f'agent/extensions/{extension_id}',
        'defaultServiceConfigPath': f'agent/extensions/{extension_id}/{profile_rel_path}',
        'manifestDir': f'agent/extensions/{extension_id}/{CONTROL_PLANE_EXTENSIONS_DIR_REL_PATH}',
        'pythonRoots': [f'agent/extensions/{extension_id}/python'],
        'status': 'managed_explicit_extension',
    }


def update_index(repo_root: Path, extension_id: str) -> None:
    index_path = repo_root / 'agent' / 'extensions' / 'index.json'
    payload = read_json(index_path) if index_path.exists() else {'extensions': []}
    rows = payload.get('extensions')
    if not isinstance(rows, list):
        rows = []
    normalized = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get('id') or '').strip() != extension_id
    ]
    normalized.append(extension_row(extension_id))
    payload['extensions'] = normalized
    write_json(index_path, payload)


def write_control_plane_manifests(
    *,
    repo_root: Path,
    extension_id: str,
    service_path: Path,
    manifest_path: Path,
    runtime_paths_path: Path,
    testing_manifest_path: Path,
    diagnostic_surface_path: Path,
) -> None:
    write_json(
        service_path,
        {
            'extends': repo_anchored_path(CONTROL_PLANE_SERVICE_CONFIG_REL_PATH),
            'extensions': {
                'manifestsDirs': [
                    repo_anchored_path(CONTROL_PLANE_EXTENSIONS_DIR_REL_PATH),
                    extension_anchored_path(CONTROL_PLANE_EXTENSIONS_DIR_REL_PATH),
                ],
                'enabledExtensionIds': ['agent_platform', extension_id],
            },
        },
    )
    write_json(
        manifest_path,
        {
            'id': extension_id,
            'title': 'Managed Probe Extension Package',
            'registry': {
                'jobsDirs': [extension_anchored_path('agent/control_plane/jobs')],
                'modelsDirs': [extension_anchored_path('agent/control_plane/models')],
                'targetsDirs': [extension_anchored_path('agent/control_plane/targets')],
                'agentGroupsDirs': [extension_anchored_path('agent/control_plane/groups')],
                'agentModulesDirs': [extension_anchored_path('agent/modules')],
                'dispatchTargetRegistryPaths': [
                    extension_anchored_path('agent/control_plane/registries/dispatch_targets.json'),
                ],
            },
            'schemas': {
                'agentGroupsSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/agent_group.schema.json'),
                'agentModulesSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/agent_module.schema.json'),
                'agentsSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/agent.schema.json'),
                'implementationsSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/implementation.schema.json'),
                'skillSetsSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/skill_set.schema.json'),
                'permissionPoliciesSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/permission_policy.schema.json'),
                'toolsetsSchema': repo_anchored_path(f'{CONTROL_PLANE_SCHEMAS_REL_DIR}/toolset.schema.json'),
            },
            SURFACE_FRAGMENTS_FIELD: {
                'runtimePathsPath': runtime_paths_path.name,
                'testingManifestPath': testing_manifest_path.name,
            },
            GOVERNANCE_SURFACES_FIELD: {
                'diagnosticSurfacePath': diagnostic_surface_path.name,
            },
        },
    )
    write_json(
        runtime_paths_path,
        {
            'entries': {
                PROBE_RUNTIME_ENTRY_ID: {
                    'kind': 'runtime_dir',
                    'category': 'artifact',
                    'owner': ['host', 'scheduler'],
                    'create_on_bootstrap': True,
                    'paths': {
                        'host': '{host_control_plane_root}/probe_dispatch_out',
                        'scheduler': '{scheduler_control_plane_root}/probe_dispatch_out',
                    },
                    'env_names': {
                        'host': 'HOST_PROBE_DISPATCH_OUT_DIR',
                        'scheduler': 'PROBE_DISPATCH_OUT_DIR',
                    },
                    'logical_group': 'probe_outputs',
                }
            },
            'logical_groups': {
                'probe_outputs': {
                    'label': 'Probe Outputs',
                    'description': 'Managed probe extension runtime outputs.',
                }
            },
        },
    )
    write_json(
        testing_manifest_path,
        {
            'valid_groups': [PROBE_TEST_GROUP_ID],
            'groups': [
                {
                    'id': PROBE_TEST_GROUP_ID,
                    'title': 'Probe Pipeline Checks',
                    'selectable': True,
                    'summary': 'Validate the managed probe extension pipeline contract.',
                }
            ],
            'checks': [
                {
                    'id': PROBE_CHECK_ID,
                    'group': PROBE_TEST_GROUP_ID,
                    'title': 'Probe chain contract',
                    'summary': 'Validate the managed probe job, group, model and target assembly.',
                }
            ],
            'release_gate_checks': [
                {
                    'id': PROBE_RELEASE_CHECK_ID,
                    'title': 'agent_probe module smoke checks',
                    'summary': 'Run the managed probe module smoke checks.',
                    'command': {
                        'script': 'scripts/doctor/check_agent_module_smoke_tests.sh',
                        'args': [
                            '--extension',
                            '{extension_id}',
                        ],
                    },
                }
            ],
            'execution_order': [PROBE_TEST_GROUP_ID],
            'acceptance_reference': {
                'required_checks': [PROBE_CHECK_ID],
                'required_run_ledger_jobs': [PROBE_JOB_REF],
            }
        },
    )
    write_json(
        diagnostic_surface_path,
        {
            'actions': {
                'actions': [
                    {
                        'action': PROBE_DIAGNOSTIC_ACTION,
                        'title': 'Run Probe',
                        'meaning': 'Run the managed probe extension regression path.',
                        'typicalAgents': [PROBE_PRIMARY_MODULE_REF],
                    }
                ]
            },
            'diagnostics': {
                'blockingGroups': [],
                'sourceDiagnosisGroups': [],
            },
            'reasons': {
                'routeHintReasons': [],
                'manualVerifyTaskReasons': [],
                'manualVerifyResultReasons': [],
                'manualVerifyBlockingReasons': [],
            },
        },
    )


def write_module_fixture(
    *,
    extension_id: str,
    module_dir: Path,
    module_ref: str,
    runtime_module: str,
    source_paths: list[str],
    entrypoint_kind: str,
    external_dispatch: bool,
    operations: dict[str, Any],
    filesystem_write: list[str],
) -> None:
    title = 'Probe Dispatcher' if module_ref == PROBE_PRIMARY_MODULE_REF else 'Probe Helper'
    write_json(
        module_dir / 'module.json',
        module_payload(
            extension_id,
            module_ref=module_ref,
            title=title,
            implementation_ref=f'{module_ref}_impl',
            entrypoint_kind=entrypoint_kind,
            external_dispatch=external_dispatch,
            runtime_module=runtime_module,
            source_paths=source_paths,
            filesystem_write=filesystem_write,
            operations=operations,
        ),
    )
    write_text(module_dir / 'README.md', module_readme(extension_id, module_ref))
    write_text(module_dir / 'skills.md', module_skills(module_ref))
    write_json(module_dir / 'permissions.json', module_permissions(module_ref))
    write_json(module_dir / 'tools.json', module_tools(module_ref))
    write_text(module_dir / 'bin' / module_ref, module_launcher(module_ref, extension_id), executable=True)
