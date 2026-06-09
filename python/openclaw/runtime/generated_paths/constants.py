"""运行态路径派生产物共享常量。"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from openclaw.lib.cli.examples import canonical_cli_command
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))
CONTROL_PLANE_CONTAINER_ROOT = PurePosixPath('/opt/openclaw-tools')
RENDER_GENERATED_RUNTIME_PATHS_CMD = canonical_cli_command('runtime', 'paths', 'render-generated')
