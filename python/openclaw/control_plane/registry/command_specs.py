#!/usr/bin/env python3
"""Canonical command specs for control-plane execution surfaces."""
from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Iterable


DIRECT_CONTROL_PLANE_EXEC = 'direct_control_plane_exec'
SCHEDULER_SERVICE_EXEC = 'scheduler_service_exec'
OPENCLAW_CLI_MODULE = 'openclaw.cli'
HOST_PYTHON_TOOL_REL_PATH = 'scripts/runtime/run_openclaw_python_tool.sh'
SUPPORTED_EXEC_MODES = frozenset({DIRECT_CONTROL_PLANE_EXEC, SCHEDULER_SERVICE_EXEC})


@dataclass(frozen=True)
class OpenClawCommandSpec:
    """A canonical command description before choosing the concrete launcher."""

    exec_mode: str
    argv: tuple[str, ...]


def _normalize_argv(argv: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(item) for item in argv if str(item).strip())


def runtime_passthrough_args(extra_args: list[str] | None = None) -> list[str]:
    normalized = [str(item) for item in list(extra_args or []) if str(item).strip()]
    if not normalized:
        return []
    return ['--', *normalized]


def build_command_spec(*argv: object, exec_mode: str = DIRECT_CONTROL_PLANE_EXEC) -> OpenClawCommandSpec:
    normalized_mode = str(exec_mode or '').strip()
    if normalized_mode not in SUPPORTED_EXEC_MODES:
        raise ValueError(f'unsupported control-plane exec mode: {normalized_mode or "<empty>"}')
    return OpenClawCommandSpec(exec_mode=normalized_mode, argv=_normalize_argv(argv))


def build_agent_runtime_command_spec(
    *,
    agent: dict[str, object],
    extra_args: list[str] | None = None,
    config_path: str | None = None,
    exec_mode: str = DIRECT_CONTROL_PLANE_EXEC,
) -> OpenClawCommandSpec:
    agent_ref = str(agent.get('qualifiedId') or agent.get('id') or '').strip()
    command = [
        'control-plane',
        'runtime',
        'run-agent-runtime' if exec_mode == DIRECT_CONTROL_PLANE_EXEC else 'scheduler-run-agent-runtime',
        '--agent-ref',
        agent_ref,
    ]
    normalized_config_path = str(config_path or '').strip()
    if normalized_config_path:
        command.extend(['--config-path', normalized_config_path])
    command.extend(runtime_passthrough_args(extra_args))
    return build_command_spec(*command, exec_mode=exec_mode)


def materialize_command(
    spec: OpenClawCommandSpec,
    *,
    python_executable: str | None = None,
    host_python_tool_rel_path: str = HOST_PYTHON_TOOL_REL_PATH,
) -> list[str]:
    if spec.exec_mode == DIRECT_CONTROL_PLANE_EXEC:
        return [
            str(python_executable or sys.executable or 'python3'),
            '-m',
            OPENCLAW_CLI_MODULE,
            *spec.argv,
        ]
    if spec.exec_mode == SCHEDULER_SERVICE_EXEC:
        return ['bash', f'./{host_python_tool_rel_path}', *spec.argv]
    raise ValueError(f'unsupported control-plane exec mode: {spec.exec_mode}')
