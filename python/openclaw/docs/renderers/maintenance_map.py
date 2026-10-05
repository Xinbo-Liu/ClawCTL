#!/usr/bin/env python3
"""从基座和默认平台真源生成维护地图，不嵌入业务 profile 或本机运行状态。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from openclaw.control_plane.facts import MAINTENANCE_MAP_DOC, build_overview_payload, render_overview_markdown
from openclaw.docs.support.generated_output import add_output_modes, publish_document, read_document
from openclaw.docs.support.markdown_tables import format_markdown_tables
from openclaw.lib.repo.layout import DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID, DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH, resolve_repo_root
from openclaw.lib.repo.verification_tiers import verification_tier_rows

ROOT_DIR = resolve_repo_root(Path(__file__))


def _profile_row(payload: dict[str, Any]) -> dict[str, Any]:
    """从单个选定 profile 的事实生成地图行，避免遍历业务 profile 的运行装配。

    参数：
        payload（dict[str, Any]）：包含选定配置、registry 输入与 evidence 定义的只读事实汇总。

    返回：
        dict[str, Any]：profile 身份、配置相对路径、启用扩展、registry 数量与 evidence 路径数量。
    """
    selected = payload['selected_config']
    inputs = payload['extensions']['registry_inputs']
    return {
        'id': selected['profile_id'],
        'config_relpath': selected['config_relpath'],
        'default_profile': selected['profile_id'] == DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,
        'enabled_extension_ids': payload['extensions']['enabled_extension_ids'],
        'registry_input_counts': {key: len(value) for key, value in inputs.items()},
        'evidence_path_count': sum(len(row['entries']) for row in payload['evidence'] if row['runtime_evidence_family']),
    }


def render_doc(*, config_path: Path | None = None) -> str:
    """渲染 base 与 agent_platform 的固定维护地图，并链接真实扩展索引。

    参数：
        config_path（Path | None）：显式 service 配置，仅接受默认平台路径；缺省使用该默认配置。

    返回：
        str：真源、派生项、默认运行服务及产物位置的 Markdown；不包含业务 profile 快照或匿名别名。

    异常：
        ValueError：传入非默认平台 service。

    副作用：
        只读取仓库真源，不探测 deploy/.env 或运行态 state。
    """
    canonical_config = ROOT_DIR / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
    if config_path is not None and config_path.resolve() != canonical_config.resolve():
        raise ValueError('维护地图固定使用 agent_platform；其他 profile 请通过 facts overview 查询')
    payloads = [
        build_overview_payload(
            config_path=path,
            probe_local=False,
            include_all_profiles=False,
            include_profile_runtime_services=False,
            include_profile_evidence_paths=False,
            root_dir=ROOT_DIR,
            scope='selected_config',
            path_environment={},
        )
        for path in (ROOT_DIR / 'config/control_plane/service.json', canonical_config)
    ]
    payload = payloads[1]
    payload['profile_overviews'] = [_profile_row(item) for item in payloads]
    payload['verification_commands'] = verification_tier_rows(ROOT_DIR)
    content = render_overview_markdown(payload, redact_managed_extensions=False)
    content = content.replace('## 默认 profile 与 extension profile', '## 基座与默认平台 profile')
    intro = '本页由 `control-plane facts overview` 的只读事实汇总生成，用于定位配置真源、生成文档、运行服务与证据路径。'
    return format_markdown_tables(content.replace(intro, intro + '\n\n固定快照只包含 `base` 与默认 `agent_platform`。扩展能力从[仓内扩展索引](../../agent/extensions/README.md)进入；实际 profile 与本机状态通过下列只读命令查询。'))


def render_entry(argv: list[str] | None = None) -> int:
    """解析维护地图生成模式，在默认写入前比较原文档身份与内容。

    参数：
        argv（list[str] | None）：命令参数；缺省使用进程参数。

    返回：
        int：成功为 0，生成漂移为 1，输入或安全校验失败为 2。

    副作用：
        默认模式原子替换维护地图；互斥的 check/stdout 模式只输出文本。
    """
    parser = argparse.ArgumentParser(prog='docs render-maintenance-map')
    add_output_modes(parser)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--control-plane-profile', choices=(DEFAULT_RUNTIME_CONTROL_PLANE_PROFILE_ID,))
    selection.add_argument('--config-path', type=Path)
    args = parser.parse_args(argv)
    try:
        target = ROOT_DIR / MAINTENANCE_MAP_DOC
        snapshot = read_document(ROOT_DIR, target)
        return publish_document(ROOT_DIR, target, render_doc(config_path=args.config_path), snapshot, check=args.check, stdout=args.stdout, label='render_maintenance_map')
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        sys.stderr.write(f'[render_maintenance_map][FAIL] {exc}\n')
        return 2


def main(argv: list[str] | None = None) -> int:
    """处理维护地图模块命令，返回生成入口的模式与校验结果。

    参数：
        argv（list[str] | None）：显式命令参数；None 时由解析器使用当前进程参数。

    返回：
        int：成功为 0，生成内容漂移为 1，合同或受控写入失败为 2。

    异常：
        SystemExit：解析器处理帮助参数或拒绝非法参数时退出。

    副作用：
        通过 render_entry 执行只读检查、标准输出或受控文档写入。
    """
    return render_entry(argv)


if __name__ == '__main__':
    raise SystemExit(main())
