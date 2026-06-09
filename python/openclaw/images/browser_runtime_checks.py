#!/usr/bin/env python3
"""official gateway 浏览器运行时校验辅助。"""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path


def _command_on_path(name: str) -> bool:
    path_value = os.environ.get('PATH') or ''
    if not path_value:
        return False
    suffixes = ['']
    if os.name == 'nt':
        suffixes.extend([item for item in (os.environ.get('PATHEXT') or '.EXE;.BAT;.CMD').split(';') if item])
    for directory in path_value.split(os.pathsep):
        if not directory:
            continue
        for suffix in suffixes:
            candidate = Path(directory) / f'{name}{suffix}'
            if candidate.exists() and candidate.is_file():
                return True
    return False


def command_gateway() -> int:
    try:
        sync_api = importlib.import_module('playwright.sync_api')
        sync_playwright = getattr(sync_api, 'sync_playwright')
    except (ModuleNotFoundError, AttributeError) as exc:
        raise SystemExit('当前解释器缺少 playwright.sync_api.sync_playwright') from exc

    if not _command_on_path("openclaw"):
        raise SystemExit("gateway image must expose openclaw CLI")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<html><head><title>OpenClaw Browser Smoke</title></head><body>ok</body></html>")
        title = page.title()
        browser.close()
    if not title:
        raise SystemExit("页面标题为空")
    print("[smoke] official gateway browser ok: " + title)
    print("[smoke] official gateway cli present")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browser_runtime_checks.py")
    parser.add_argument("mode", choices=("gateway",))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == 'gateway':
        return command_gateway()
    raise SystemExit(f'unsupported mode: {args.mode}')


if __name__ == "__main__":
    raise SystemExit(main())
