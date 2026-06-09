#!/usr/bin/env python3
"""Shared command example builders for docs and static control surfaces."""
from __future__ import annotations

import shlex


OPENCLAW_CLI_PROGRAM = 'openclaw'
HOST_PYTHON_TOOL_REL_PATH = 'scripts/runtime/run_openclaw_python_tool.sh'

def shell_join(argv: list[str]) -> str:
    return ' '.join(shlex.quote(str(item)) for item in argv)


def canonical_cli_args(*args: str) -> list[str]:
    return [str(item) for item in args]


def canonical_cli_command(*args: str, program: str = OPENCLAW_CLI_PROGRAM) -> str:
    return shell_join([program, *canonical_cli_args(*args)])


def host_wrapper_command(*args: str) -> str:
    return shell_join(['bash', f'./{HOST_PYTHON_TOOL_REL_PATH}', *canonical_cli_args(*args)])


def usage_block(*commands: str, title: str = 'Usage:', trailing_newline: bool = True) -> str:
    block = '\n'.join([title, *(f'  {command}' for command in commands)])
    return block + ('\n' if trailing_newline else '')
