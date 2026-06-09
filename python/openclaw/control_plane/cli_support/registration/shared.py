#!/usr/bin/env python3
"""control-plane 分组 CLI 注册的共享辅助。"""
from __future__ import annotations

import argparse
from collections.abc import Callable


def add_config_path(parser: argparse.ArgumentParser, *, default_config: str) -> None:
    parser.add_argument('--config-path', default='')
    parser.add_argument('--control-plane-profile', default='')
    parser.set_defaults(_default_config_path=default_config)


def add_explicit_or_profile_config(parser: argparse.ArgumentParser) -> None:
    add_config_path(parser, default_config='')


def register_config_only(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    default_config: str,
    command: str,
    help_text: str,
    handler: Callable[..., object],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(command, help=help_text)
    add_config_path(parser, default_config=default_config)
    parser.set_defaults(func=handler)
    return parser
