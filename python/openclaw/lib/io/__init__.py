"""跨模块 IO 公共辅助。"""

from .state import (
    append_jsonl,
    ensure_dir,
    read_json_if_exists,
    read_json_state,
    read_text_state,
    with_lock_dir,
    write_json_atomic,
    write_text_atomic,
)

__all__ = [
    "append_jsonl",
    "ensure_dir",
    "read_json_if_exists",
    "read_json_state",
    "read_text_state",
    "with_lock_dir",
    "write_json_atomic",
    "write_text_atomic",
]
