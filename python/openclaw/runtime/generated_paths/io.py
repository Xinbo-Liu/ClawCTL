"""运行态派生产物读写工具。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding='utf-8')


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(read_text(path))
