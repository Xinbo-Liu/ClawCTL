#!/usr/bin/env python3
"""Syntax-check repository Python files without writing bytecode artifacts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Sequence

from openclaw.lib.repo.layout import resolve_repo_root

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "extension_envs",
    "venv",
}


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_python_files(root: Path, paths: Iterable[Path]) -> Iterable[Path]:
    repo_root = Path(root).resolve()
    for item in paths:
        target = item if item.is_absolute() else repo_root / item
        resolved = target.resolve()
        try:
            resolved.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f"syntax check path must stay inside repository: {item}") from exc
        if _is_skipped(resolved.relative_to(repo_root)):
            continue
        if resolved.is_file():
            if resolved.suffix == ".py":
                yield resolved
            continue
        if not resolved.is_dir():
            raise FileNotFoundError(f"syntax check path does not exist: {resolved}")
        for path in sorted(resolved.rglob("*.py")):
            relative = path.resolve().relative_to(repo_root)
            if not _is_skipped(relative):
                yield path.resolve()


def check_python_syntax(root: Path, paths: Iterable[Path]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    repo_root = Path(root).resolve()
    for path in iter_python_files(repo_root, paths):
        relative = path.relative_to(repo_root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec", dont_inherit=True)
        except SyntaxError as exc:
            failures.append(
                {
                    "path": relative,
                    "line": str(exc.lineno or ""),
                    "offset": str(exc.offset or ""),
                    "error": exc.msg,
                }
            )
        except UnicodeDecodeError as exc:
            failures.append({"path": relative, "line": "", "offset": "", "error": str(exc)})
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="syntax-check Python files without creating __pycache__ or .pyc artifacts")
    parser.add_argument("paths", nargs="*", help="repo-relative files or directories; defaults to python and managed extension sources/tests")
    return parser


def default_paths(root: Path) -> list[Path]:
    paths = [Path("python")]
    extensions = root / "agent" / "extensions"
    if extensions.is_dir():
        for extension in sorted(path for path in extensions.iterdir() if path.is_dir()):
            for rel in ("python", "tests"):
                target = extension / rel
                if target.exists():
                    paths.append(target.relative_to(root))
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    root = resolve_repo_root(Path(__file__))
    args = build_parser().parse_args(list(argv or sys.argv[1:]))
    paths = [Path(item) for item in args.paths] if args.paths else default_paths(root)
    failures = check_python_syntax(root, paths)
    if failures:
        for failure in failures:
            location = failure["path"]
            if failure["line"]:
                location = f"{location}:{failure['line']}"
                if failure["offset"]:
                    location = f"{location}:{failure['offset']}"
            print(f"[syntax_check][FAIL] {location}: {failure['error']}", file=sys.stderr)
        return 1
    print(f"[syntax_check] checked {len(list(iter_python_files(root, paths)))} Python files without bytecode output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
