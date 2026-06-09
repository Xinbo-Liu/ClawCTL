"""getting-started 文档渲染。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.surfaces import load_deploy_env_schema, load_testing_manifest
from openclaw.docs.renderers.getting_started_support.fragments import (
    default_flow_steps,
    quickstart_step2_note_lines,
    render_bullets,
    render_code_block,
    render_numbered,
    render_paragraphs,
    render_powershell_block,
    render_step_sections,
    render_text_block,
)
from openclaw.docs.renderers.getting_started_support.loaders import (
    ROOT_DIR,
    SECTIONS_PATH,
    conditional_manual_field_lines,
    fail,
    ingress_manual_fields,
    load_sections,
    load_surface,
    read_json,
    required_manual_field_lines,
    required_manual_fields,
    sorted_fields,
)
from openclaw.docs.renderers.getting_started_support.pages import (
    environment_setup_doc as _environment_setup_doc,
    quickstart_doc as _quickstart_doc,
    render_docs as _render_docs,
)
from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.layout import (
    DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    resolve_selected_control_plane_config_path,
)
from openclaw.lib.repo.static_truth import repo_contract_path


RENDER_GETTING_STARTED_CMD = canonical_cli_command('docs', 'render-getting-started')

QUICKSTART_NOTICE = ''
ENV_NOTICE = QUICKSTART_NOTICE


def quickstart_doc(
    surface: dict[str, Any],
    sections: dict[str, Any],
    schema: dict[str, Any],
    baseline: dict[str, Any],
    control_plane_medium: dict[str, Any],
    setup_entrypoints: dict[str, Any],
    testing_manifest: dict[str, Any],
) -> str:
    return _quickstart_doc(
        surface,
        sections,
        schema,
        baseline,
        control_plane_medium,
        setup_entrypoints,
        testing_manifest,
        quickstart_notice=QUICKSTART_NOTICE,
    )


def environment_setup_doc(surface: dict[str, Any], sections: dict[str, Any], schema: dict[str, Any], control_plane_medium: dict[str, Any]) -> str:
    return _environment_setup_doc(
        surface,
        sections,
        schema,
        control_plane_medium,
        env_notice=ENV_NOTICE,
        ingress_manual_fields=ingress_manual_fields,
    )


def render_docs(surface: dict[str, Any], schema: dict[str, Any], baseline: dict[str, Any], control_plane_medium: dict[str, Any], setup_entrypoints: dict[str, Any], testing_manifest: dict[str, Any]) -> dict[str, str]:
    return _render_docs(
        surface,
        schema,
        baseline,
        control_plane_medium,
        setup_entrypoints,
        testing_manifest,
        quickstart_notice=QUICKSTART_NOTICE,
        env_notice=ENV_NOTICE,
        ingress_manual_fields=ingress_manual_fields,
    )


def render_entry(argv: list[str]) -> int:
    mode = 'write'
    config_path: str | Path | None = None
    control_plane_profile = ''
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--check':
            mode = 'check'
        elif arg == '--stdout':
            mode = 'stdout'
        elif arg == '--config-path':
            index += 1
            if index >= len(argv):
                fail('--config-path 缺少路径参数')
            config_path = argv[index]
        elif arg.startswith('--config-path='):
            value = arg.split('=', 1)[1].strip()
            if not value:
                fail('--config-path 缺少路径参数')
            config_path = value
        elif arg == '--control-plane-profile':
            index += 1
            if index >= len(argv):
                fail('--control-plane-profile 缺少 profile 参数')
            control_plane_profile = str(argv[index] or '').strip()
            if not control_plane_profile:
                fail('--control-plane-profile 缺少 profile 参数')
        elif arg.startswith('--control-plane-profile='):
            control_plane_profile = arg.split('=', 1)[1].strip()
            if not control_plane_profile:
                fail('--control-plane-profile 缺少 profile 参数')
        elif arg in {'-h', '--help'}:
            sys.stdout.write(
                '用法：\n'
                f'  {RENDER_GETTING_STARTED_CMD} [--control-plane-profile <profile> | --config-path <path>]\n'
                f'  {RENDER_GETTING_STARTED_CMD} --check [--control-plane-profile <profile> | --config-path <path>]\n'
                f'  {RENDER_GETTING_STARTED_CMD} --stdout [--control-plane-profile <profile> | --config-path <path>]\n'
            )
            return 0
        else:
            fail(f'未知参数：{arg}')
        index += 1

    if config_path is not None and control_plane_profile:
        fail('--config-path 与 --control-plane-profile 不能同时使用')
    resolved_config_path = resolve_selected_control_plane_config_path(
        config_path,
        control_plane_profile=control_plane_profile or (DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID if config_path is None else None),
        start_path=ROOT_DIR,
        default_profile=DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
    )
    surface = load_surface()
    schema = load_deploy_env_schema(config_path=resolved_config_path)
    baseline = read_json(repo_contract_path('governance.default_deployment_flow'))
    control_plane_medium = read_json(repo_contract_path('setup.control_plane_medium'))
    setup_entrypoints = read_json(repo_contract_path('governance.setup_entrypoints'))
    testing_manifest = load_testing_manifest(config_path=resolved_config_path)
    rendered = render_docs(surface, schema, baseline, control_plane_medium, setup_entrypoints, testing_manifest)

    if mode == 'stdout':
        first = True
        for rel_path, content in rendered.items():
            if not first:
                sys.stdout.write('\n\n')
            first = False
            sys.stdout.write(f'===== {rel_path} =====\n')
            sys.stdout.write(content)
        return 0

    mismatches: list[str] = []
    for rel_path, content in rendered.items():
        target_path = ROOT_DIR / rel_path
        existing = target_path.read_text(encoding='utf-8') if target_path.exists() else None
        if mode == 'check':
            if existing != content:
                mismatches.append(rel_path)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding='utf-8', newline='\n')
        sys.stdout.write(f'[getting_started_reference] 已写入 {rel_path}\n')
    if mode == 'check':
        if mismatches:
            sys.stderr.write('[getting_started_reference] 文档未同步：\n- ' + '\n- '.join(mismatches) + '\n')
            return 1
        sys.stdout.write('[getting_started_reference] 已同步\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    return render_entry(list(sys.argv[1:] if argv is None else argv))


if __name__ == '__main__':
    raise SystemExit(main())
