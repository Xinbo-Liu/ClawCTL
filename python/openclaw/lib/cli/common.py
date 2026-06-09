#!/usr/bin/env python3
"""跨模块 CLI 公共辅助。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


class CliError(RuntimeError):
    """CLI 执行失败。"""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(str(message or "未知错误"))
        self.exit_code = int(exit_code) or 2


def fail(message: str, exit_code: int = 2) -> NoReturn:
    raise CliError(message, exit_code)


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def to_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def to_array(value: object) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def parse_flag_args(argv: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"_positionals": []}
    index = 0
    while index < len(argv):
        current = argv[index]
        if current.startswith("--"):
            key = current[2:]
            if "=" in key:
                inline_key, inline_value = key.split("=", 1)
                result[inline_key] = inline_value
                index += 1
                continue
            next_value = argv[index + 1] if index + 1 < len(argv) else None
            if next_value is None or str(next_value).startswith("--"):
                result[key] = True
                index += 1
                continue
            result[key] = next_value
            index += 2
            continue
        result["_positionals"].append(current)
        index += 1
    return result


@dataclass(frozen=True)
class FlagSpec:
    kind: str = "str"
    dest: str | None = None
    default: Any = None
    choices: tuple[str, ...] = ()
    allow_empty: bool = False


def parse_typed_flag_args(
    argv: list[str],
    *,
    specs: dict[str, FlagSpec],
    allow_positionals: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    raw = parse_flag_args(list(argv))
    positionals = [str(item) for item in raw.pop("_positionals", [])]
    if not allow_positionals and positionals:
        raise CliError(f'未知参数：{" ".join(positionals)}')
    unknown = [f'--{flag}' for flag in raw if flag not in specs]
    if unknown:
        raise CliError(f'未知参数：{" ".join(sorted(unknown))}')
    values: dict[str, Any] = {}
    for flag, spec in specs.items():
        values[spec.dest or flag.replace("-", "_")] = spec.default
    for flag, value in raw.items():
        spec = specs[flag]
        dest = spec.dest or flag.replace("-", "_")
        if spec.kind == "bool":
            values[dest] = True if value is True else parse_bool(value, default=False)
            continue
        if value is True:
            raise CliError(f'--{flag} 缺少参数')
        text = str(value)
        if not spec.allow_empty and not text.strip():
            raise CliError(f'--{flag} 缺少参数')
        if spec.kind == "path":
            values[dest] = Path(text).resolve()
            continue
        normalized = text.strip() if not spec.allow_empty else text
        if spec.choices and normalized not in set(spec.choices):
            raise CliError(f'--{flag} 不支持：{normalized}')
        values[dest] = normalized
    return values, positionals
