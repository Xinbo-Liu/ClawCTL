from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openclaw.docs.support import reference_specs as specs

ROOT_DIR = specs.ROOT_DIR


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='重渲染 workspace USER 自动生成段')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--config-path', default=None)
    return parser.parse_args(list(sys.argv[1:] if argv is None else argv))


def render_entry(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config_path = Path(args.config_path).resolve() if args.config_path else None
    rendered = (
        specs.render_workspace_user_targets(ROOT_DIR, config_path=config_path)
        if config_path is not None
        else specs.render_workspace_user_targets_for_repo(ROOT_DIR)
    )
    bad = [p for p, c in rendered.items() if p.read_text(encoding='utf-8') != c]
    if args.check:
        if not bad:
            sys.stdout.write('[render_workspace_user_sections] 已同步\n')
            return 0
        sys.stderr.write('[render_workspace_user_sections] 文档未同步：\n- ' + '\n- '.join(str(p.relative_to(ROOT_DIR)) for p in bad) + '\n')
        return 1
    for path, content in rendered.items():
        path.write_text(content, encoding='utf-8', newline='\n')
        sys.stdout.write(f'[render_workspace_user_sections] 已写入 {path.relative_to(ROOT_DIR)}\n')
    return 0


def main(argv: list[str] | None = None) -> int:
    return render_entry(argv)


if __name__ == '__main__':
    raise SystemExit(main())
