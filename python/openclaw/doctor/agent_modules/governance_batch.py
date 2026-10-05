#!/usr/bin/env python3
"""共享受管扩展 profile、registry 与文本索引执行静态治理检查。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from openclaw.control_plane.registry import load_registry
from openclaw.doctor.agent_governance.baseline_support import run_governance_baseline_check
from openclaw.doctor.agent_modules import job_surface, optional_surface, runtime_script_orphans
from openclaw.doctor.release.repo_release_gate_support import CheckSpec, managed_extension_release_checks
from openclaw.lib.repo.layout import resolve_repo_root, resolve_selected_control_plane_config_path


ROOT_DIR = resolve_repo_root(Path(__file__))
SUPPORTED_SCRIPT_RELS = {
    'scripts/doctor/check_agent_runtime_script_orphans.sh',
    'scripts/doctor/check_agent_governance_baseline.sh',
    'scripts/doctor/check_agent_module_optional_surface.sh',
    'scripts/doctor/check_agent_job_surface.sh',
}


def _script_rel(spec: CheckSpec) -> str:
    """提取检查命令中的仓库相对脚本路径。

    参数：
        spec（CheckSpec）：testing manifest 装配出的检查定义。

    返回：
        str：仓库内脚本路径；命令不完整或越界时为空字符串。
    """
    command = list(spec.command)
    if len(command) < 2:
        return ''
    try:
        return Path(str(command[1])).resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return ''


def _flag_value(spec: CheckSpec, flag: str) -> str:
    """读取检查命令中指定标志紧随的参数值。

    参数：
        spec（CheckSpec）：待解析的检查定义。
        flag（str）：需要定位的完整命令行标志。

    返回：
        str：标志后的参数；标志或参数缺失时为空字符串。
    """
    command = [str(item) for item in spec.command]
    try:
        index = command.index(flag)
    except ValueError:
        return ''
    return command[index + 1] if index + 1 < len(command) else ''


def governance_check_specs() -> list[CheckSpec]:
    """选择可以共享扩展治理上下文的静态发布检查。

    返回：
        list[CheckSpec]：保持 testing manifest 注册顺序的批处理检查定义。
    """
    return [
        spec
        for spec in managed_extension_release_checks()
        if spec.lane == 'static' and _script_rel(spec) in SUPPORTED_SCRIPT_RELS
    ]


def _config_path(profile_id: str) -> Path:
    """解析扩展检查声明的控制平面 profile。

    参数：
        profile_id（str）：testing manifest 渲染后的 profile 标识。

    返回：
        Path：对应的控制平面服务配置绝对路径。

    异常：
        ValueError：profile 未登记或配置路径不符合仓库约束时抛出。
    """
    return resolve_selected_control_plane_config_path(
        None,
        control_plane_profile=profile_id,
        start_path=ROOT_DIR,
        default_to_base=True,
    ).resolve()


def _failed(payload: dict[str, Any]) -> bool:
    """按现有治理载荷的 ``ok`` 与 ``errors`` 语义判断失败。

    参数：
        payload（dict[str, Any]）：单个治理检查生成的机器载荷。

    返回：
        bool：载荷明确拒绝或包含错误时为真。
    """
    if payload.get('ok') is False:
        return True
    return bool(payload.get('errors'))


def build_report(specs: Sequence[CheckSpec] | None = None) -> dict[str, Any]:
    """一次加载共享输入并执行受管扩展静态治理检查。

    参数：
        specs（Sequence[CheckSpec] | None）：待执行检查；为空时使用全部可批处理声明。

    返回：
        dict[str, Any]：保持原 check ID 的状态、耗时、退出码与诊断详情。

    副作用：
        读取受管扩展配置、registry 与仓库文本文件，不修改仓库内容。

    异常：
        子检查异常会转换为对应检查 ID 的失败详情，其他子检查继续执行。
    """
    selected = list(governance_check_specs() if specs is None else specs)
    registry_cache: dict[Path, dict[str, Any]] = {}
    needs_corpus = any(_script_rel(spec).endswith('check_agent_runtime_script_orphans.sh') for spec in selected)
    corpus = runtime_script_orphans.repo_text_files(ROOT_DIR) if needs_corpus else []
    checks: list[dict[str, Any]] = []
    for spec in selected:
        started = time.monotonic()
        script_rel = _script_rel(spec)
        try:
            if script_rel == 'scripts/doctor/check_agent_runtime_script_orphans.sh':
                payload = runtime_script_orphans.build_orphan_report(
                    ROOT_DIR,
                    extension_id=_flag_value(spec, '--extension') or None,
                    corpus=corpus,
                )
            else:
                profile_id = _flag_value(spec, '--control-plane-profile')
                config_path = _config_path(profile_id)
                if config_path not in registry_cache:
                    registry_cache[config_path] = dict(load_registry(config_path))
                registry = registry_cache[config_path]
                if script_rel == 'scripts/doctor/check_agent_governance_baseline.sh':
                    payload = run_governance_baseline_check(config_path, registry_payload=registry)
                elif script_rel == 'scripts/doctor/check_agent_module_optional_surface.sh':
                    payload = optional_surface.build_report(registry)
                elif script_rel == 'scripts/doctor/check_agent_job_surface.sh':
                    payload = job_surface.build_report(registry)
                else:
                    raise ValueError(f'unsupported agent governance batch script: {script_rel}')
            failed = _failed(payload)
            detail = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception as exc:  # noqa: BLE001 - batch must preserve sibling diagnostics
            failed = True
            detail = f'{type(exc).__name__}: {exc}'
        checks.append({
            'id': spec.check_id,
            'status': 'FAIL' if failed else 'PASS',
            'exitCode': 1 if failed else 0,
            'timedOut': False,
            'durationSeconds': round(time.monotonic() - started, 3),
            'detail': detail,
        })
    return {
        'suite': 'agent_extension_governance',
        'status': 'FAIL' if any(item['status'] == 'FAIL' for item in checks) else 'PASS',
        'checks': checks,
    }


def main(argv: list[str] | None = None) -> int:
    """执行共享扩展治理批处理并输出单个 JSON 文档。

    参数：
        argv（list[str] | None）：当前入口不接受额外参数。

    返回：
        int：所有子检查通过时为 0，否则为非零退出码。

    副作用：
        将批处理机器报告写入标准输出；参数错误写入标准错误。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(f'[agent_extension_governance][FAIL] 未知参数：{" ".join(args)}', file=sys.stderr)
        return 2
    payload = build_report()
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
