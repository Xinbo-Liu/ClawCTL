#!/usr/bin/env python3
"""deploy/.env 渲染、校验与部署输入文档控制面。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

from openclaw.lib.cli import CliError, FlagSpec, parse_typed_flag_args
from openclaw.lib.repo.layout import (
    resolve_default_runtime_control_plane_service_config_path,
    resolve_repo_root,
)
from openclaw.lib.repo.static_truth import runtime_paths_host_entry as truth_runtime_paths_host_entry
from openclaw.setup.deploy_env import dispatch_registry as deploy_env_dispatch_registry_lib
from openclaw.setup.deploy_env import bootstrap_runtime as deploy_env_bootstrap_runtime_lib
from openclaw.setup.deploy_env import docs as deploy_env_docs_lib
from openclaw.setup.deploy_env import query as deploy_env_query_lib
from openclaw.setup.deploy_env import render_validate as deploy_env_render_validate_lib
from openclaw.setup.deploy_env.support import load_schema

ROOT_DIR = resolve_repo_root(Path(__file__))
DEFAULT_OUTPUT_PATH = deploy_env_render_validate_lib.DEFAULT_OUTPUT_PATH
DEFAULT_COMPOSE_FILE = ROOT_DIR / 'deploy' / 'docker-compose.yml'
DEFAULT_RUNTIME_SCHEDULER_APP_ENV_PATH = ROOT_DIR / truth_runtime_paths_host_entry('runtime_scheduler_app_env')
DEFAULT_RUNTIME_INTERNAL_API_APP_ENV_PATH = ROOT_DIR / truth_runtime_paths_host_entry('runtime_internal_api_app_env')
DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH = ROOT_DIR / truth_runtime_paths_host_entry('dispatch_targets_json')
DEFAULT_DISPATCH_RUNTIME_SUMMARY_PATH = ROOT_DIR / truth_runtime_paths_host_entry('dispatch_runtime_summary_json')
DEFAULT_RUNTIME_INTERNAL_API_BIND = str(os.environ.get('OPENCLAW_RENDER_RUNTIME_INTERNAL_API_BIND') or '').strip() or '0.0.0.0'


def fail(prefix: str, message: str, exit_code: int = 2) -> NoReturn:
    sys.stderr.write(f'[{prefix}][FAIL] {message}\n')
    raise SystemExit(exit_code)


def note(prefix: str, message: str) -> None:
    sys.stdout.write(f'[{prefix}] {message}\n')


def docs_entry(argv: list[str]) -> int:
    """分发部署输入文档相关子命令。

    参数：
        argv: `docs` 后面的子命令与参数，例如 `render-deployment-inputs --check` 或
            `render-site-env-example --output deploy/site.env.example`。
    返回：
        子命令退出码；`check` 模式发现漂移时返回 1，写入或同步时返回 0。
    副作用：
        write 模式会写入部署输入说明或 `deploy/site.env.example`；`stdout` 模式只打印内容。
    失败：
        缺少子命令、未知子命令、未知参数或真源不可解析时通过 `SystemExit` 失败。
    """
    if not argv:
        fail('deploy_env_control_plane', '缺少 docs 子命令；当前支持 render-deployment-inputs / render-site-env-example', 2)
    command = argv.pop(0)
    if command not in {'render-deployment-inputs', 'render-site-env-example'}:
        fail('deploy_env_control_plane', f'未知 docs 子命令：{command}', 2)

    try:
        values, positionals = parse_typed_flag_args(
            argv,
            specs={
                'output': FlagSpec(kind='path', dest='output_path', default=None),
                'check': FlagSpec(kind='bool', dest='check', default=False),
                'stdout': FlagSpec(kind='bool', dest='stdout', default=False),
                'config-path': FlagSpec(kind='path', dest='config_path', default=None),
            },
        )
    except CliError as exc:
        fail('deploy_env_control_plane', str(exc), 2)
    if positionals:
        fail('deploy_env_control_plane', f'未知参数：{positionals[0]}', 2)
    mode = 'write'
    if values['check']:
        mode = 'check'
    if values['stdout']:
        mode = 'stdout'

    if command == 'render-site-env-example':
        resolved_output = values['output_path'] or deploy_env_render_validate_lib.DEFAULT_SITE_ENV_EXAMPLE_PATH
        return deploy_env_docs_lib.render_site_env_example(
            resolved_output,
            mode=mode,
        )

    schema = load_schema(config_path=values['config_path'])
    resolved_output = values['output_path'] or deploy_env_docs_lib.default_deployment_inputs_doc_path(schema, root_dir=ROOT_DIR)
    return deploy_env_docs_lib.render_deployment_inputs_doc(
        resolved_output,
        root_dir=ROOT_DIR,
        mode=mode,
        config_path=values['config_path'],
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if not args:
        fail(
            'deploy_env_control_plane',
            '缺少命令；仅支持 render / validate / query-env / query-env-batch / docs / '
            'render-dispatch-runtime / render-runtime-service-envs / validate-dispatch-registry / '
            'query-dispatch-registry / sync-dispatch-compose-env / bootstrap-runtime / render-local-ro-mirror',
            2,
        )

    command = args.pop(0)
    if command == 'render':
        return deploy_env_render_validate_lib.render_env(args, fail=fail, note=note)
    if command == 'validate':
        return deploy_env_render_validate_lib.validate_env(args, fail=fail, note=note)
    if command == 'query-env':
        return deploy_env_query_lib.query_env_value(args, default_output_path=DEFAULT_OUTPUT_PATH, fail=fail)
    if command == 'query-env-batch':
        return deploy_env_query_lib.query_env_batch(args, default_output_path=DEFAULT_OUTPUT_PATH, fail=fail)
    if command == 'docs':
        return docs_entry(args)
    if command == 'render-dispatch-runtime':
        return deploy_env_dispatch_registry_lib.render_dispatch_runtime(
            args,
            default_env_file=DEFAULT_OUTPUT_PATH,
            default_output=DEFAULT_DISPATCH_RUNTIME_OUTPUT_PATH,
            default_summary_json=DEFAULT_DISPATCH_RUNTIME_SUMMARY_PATH,
        )
    if command == 'render-runtime-service-envs':
        return deploy_env_dispatch_registry_lib.render_runtime_service_envs(
            args,
            default_env_file=DEFAULT_OUTPUT_PATH,
            default_scheduler_output=DEFAULT_RUNTIME_SCHEDULER_APP_ENV_PATH,
            default_internal_api_output=DEFAULT_RUNTIME_INTERNAL_API_APP_ENV_PATH,
            default_config_path=resolve_default_runtime_control_plane_service_config_path(ROOT_DIR),
            default_internal_api_bind=DEFAULT_RUNTIME_INTERNAL_API_BIND,
        )
    if command == 'validate-dispatch-registry':
        return deploy_env_dispatch_registry_lib.validate_dispatch_registry(args)
    if command == 'query-dispatch-registry':
        return deploy_env_dispatch_registry_lib.query_dispatch_registry(args)
    if command == 'sync-dispatch-compose-env':
        return deploy_env_dispatch_registry_lib.sync_dispatch_compose_env(
            args,
            default_compose_file=DEFAULT_COMPOSE_FILE,
        )
    if command == 'bootstrap-runtime':
        return deploy_env_bootstrap_runtime_lib.bootstrap_runtime(args)
    if command == 'render-local-ro-mirror':
        return deploy_env_bootstrap_runtime_lib.render_local_ro_mirror_cli(args)
    fail('deploy_env_control_plane', f'未知命令：{command}', 2)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
