#!/usr/bin/env python3
"""生成和校验 one_click_test_basic 摘要、复跑命令与 basic gate proof。"""
from __future__ import annotations

import json
import hashlib
import shlex
import sys
from pathlib import Path
from typing import Any, NoReturn

from openclaw.lib.summary.io import utc_now_iso
from openclaw.lib.testing.full_test.acceptance import parse_result_line
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.source_strategy import deployment_image_roles
from openclaw.lib.repo.static_truth import host_control_plane_file
from openclaw.setup.deploy_env.query import parse_env_file
from openclaw.setup.surface import failure as failure_surface
from openclaw.setup.surface import followup as followup_surface


ENTRY_ID = 'one_click_test_basic'
PREFLIGHT_DETAIL = 'Docker / 控制面 / 静态前置未闭合，basic gate 在正式检查组前提前失败。'
REPO_ROOT = resolve_repo_root(Path(__file__))
PROOF_SCHEMA_VERSION = 1

def fail(message: str, code: int = 2) -> NoReturn:
    """输出 basic summary 控制面错误并以指定退出码终止。"""
    sys.stderr.write(f'[basic_summary_control_plane][FAIL] {message}\n')
    raise SystemExit(code)


def parse_bool(raw: object, name: str) -> bool:
    """解析命令行布尔值，拒绝模糊输入。"""
    value = str(raw).strip().lower()
    if value in {'1', 'true', 'yes'}:
        return True
    if value in {'0', 'false', 'no', ''}:
        return False
    fail(f'{name} 只接受 true/false/1/0，收到：{raw}')
    raise AssertionError('unreachable')


def parse_args(argv: list[str]) -> dict[str, Any]:
    """解析 summary/proof 子命令参数并返回统一 options 字典。"""
    opts: dict[str, Any] = {
        'format': 'text',
        'generated_at': '',
        'env_file': '',
        'offline': False,
        'return_code': 0,
        'result_lines_file': '',
        'failed_stage': '',
        'failure_detail': '',
        'image_archive_path': '',
        'proof_path': '',
        'release_check': True,
        'release_policy': 'relaxed_install',
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if not arg.startswith('--'):
            fail(f'未知参数：{arg}')
        index += 1
        if index >= len(argv):
            fail(f'{arg} 缺少参数值')
        value = argv[index]
        index += 1
        match arg:
            case '--format':
                opts['format'] = value
            case '--generated-at':
                opts['generated_at'] = value
            case '--env-file':
                opts['env_file'] = value
            case '--offline':
                opts['offline'] = parse_bool(value, '--offline')
            case '--return-code':
                opts['return_code'] = int(value)
            case '--result-lines-file':
                opts['result_lines_file'] = str(Path(value).resolve())
            case '--failed-stage':
                opts['failed_stage'] = value
            case '--failure-detail':
                opts['failure_detail'] = value
            case '--image-archive-path':
                opts['image_archive_path'] = value
            case '--proof-path':
                opts['proof_path'] = value
            case '--release-check':
                opts['release_check'] = parse_bool(value, '--release-check')
            case '--release-policy':
                if value not in {'relaxed_install', 'strict_release', 'skipped'}:
                    fail(f'--release-policy 仅支持 relaxed_install|strict_release|skipped，收到：{value}')
                opts['release_policy'] = value
            case _:
                fail(f'未知参数：{arg}')
    if not bool(opts.get('release_check', True)):
        opts['release_policy'] = 'skipped'
    return opts


def read_lines(file_path: str) -> list[str]:
    """读取 result lines 文件；路径为空时返回空列表。"""
    if not file_path:
        return []
    path = Path(file_path)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line]


def default_gate_proof_path() -> Path:
    """返回默认 latest basic gate proof 路径。"""
    return Path(host_control_plane_file('setup/one_click_test_basic.latest.proof.json', REPO_ROOT))


def gate_proof_path(options: dict[str, Any]) -> Path:
    """按显式参数或默认位置解析 proof 文件路径。"""
    raw = str(options.get('proof_path') or '').strip()
    return Path(raw).resolve() if raw else default_gate_proof_path()


def resolve_existing_or_requested_path(raw: str) -> str:
    """将路径请求规范化为绝对路径；空值保持为空。"""
    text = str(raw or '').strip()
    if not text:
        return ''
    return str(Path(text).resolve())


def resolve_image_archive_for_mode(options: dict[str, Any]) -> str:
    """离线模式下解析归档路径；未显式传入时选择最新归档。"""
    requested = resolve_existing_or_requested_path(str(options.get('image_archive_path') or ''))
    if requested or not bool(options.get('offline')):
        return requested
    artifact_dir = REPO_ROOT / 'state' / 'image_artifacts'
    candidates = [path for path in artifact_dir.glob('deployment_images_*.tar') if path.is_file()]
    if not candidates:
        return ''
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return str(latest.resolve())


def sha256_file(path: Path) -> str:
    """计算文件 sha256，作为 proof 输入指纹。"""
    if not path.is_file():
        fail(f'无法计算 env 摘要，文件不存在：{path}', 2)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def image_ref_pin_contracts(root_dir: Path = REPO_ROOT) -> dict[str, str]:
    """从 runtime source strategy 派生部署镜像 env key 与 pin 合同关系。"""
    return {role.env_key: role.pin_contract for role in deployment_image_roles(root_dir)}


def env_image_refs(env_file: Path) -> dict[str, str]:
    """读取 deploy env 与 pin 合同，形成 proof 绑定的部署镜像引用。"""
    values = parse_env_file(env_file)
    pins: dict[str, str] = {}
    pin_contracts = image_ref_pin_contracts(REPO_ROOT)
    for key, contract_id in pin_contracts.items():
        pin_path = repo_contract_path(contract_id, root_dir=REPO_ROOT)
        pin_values = parse_env_file(pin_path) if pin_path.is_file() else {}
        pins[key] = str(pin_values.get(key) or '')
    return {key: str(values.get(key) or pins.get(key) or '') for key in pin_contracts}


def _has_status(checks: list[dict[str, Any]], status: str, check_id: str) -> bool:
    """判断指定检查项是否存在某个状态。"""
    return any(item.get('status') == status and item.get('id') == check_id for item in checks)


def _collect_failure_scenarios(checks: list[dict[str, Any]]) -> list[str]:
    """根据失败项归类 setup 主链失败场景。"""
    scenarios: list[str] = []
    if any(
        _has_status(checks, 'FAIL', target)
        for target in ('env_file_exists', 'env_required_placeholders', 'verify_required_deploy_env', 'ingress_boundary_evidence_preflight')
    ):
        scenarios.append('config_failed')
    if any(_has_status(checks, 'FAIL', target) for target in ('local_runtime_fs_contract', 'runtime_bind_user_contract')):
        scenarios.append('filesystem_or_bind_user_failed')
    if _has_status(checks, 'FAIL', 'check_docker_host_readiness'):
        scenarios.append('host_readiness_failed')
    if any(_has_status(checks, 'FAIL', target) for target in ('deployment_image_readiness', 'deploy_image_coverage', 'runtime_compose_contract')):
        scenarios.append('image_or_compose_contract_failed')
    if _has_status(checks, 'WARN', 'openclaw_release_alignment'):
        scenarios.append('release_alignment_blocked')
    return scenarios


def _apply_basic_command_variants(command: str, *, offline: bool, image_archive_path: str) -> str:
    """按在线/离线模式为 basic gate 复跑命令追加必要参数。"""
    line = str(command).strip()
    if not offline:
        return line
    if line == 'bash ./scripts/setup/one_click_test_basic.sh':
        line = 'bash ./scripts/setup/one_click_test_basic.sh --offline'
        if image_archive_path:
            line += f' --image-archive {image_archive_path}'
        return line
    if line == 'bash ./scripts/doctor/check_docker_host_readiness.sh':
        return 'bash ./scripts/doctor/check_docker_host_readiness.sh --offline'
    return line


def _scenario_commands(entry_id: str, scenario_id: str, *, offline: bool, image_archive_path: str) -> list[str]:
    """读取失败场景建议命令并套用 basic gate 模式参数。"""
    scenario = failure_surface.scenario_info(entry_id, scenario_id)
    commands = failure_surface.list_str(scenario, 'commands')
    return [
        _apply_basic_command_variants(item, offline=offline, image_archive_path=image_archive_path)
        for item in commands
    ]


def _success_commands(*, offline: bool, image_archive_path: str) -> list[str]:
    """读取成功态下一步命令，并按当前模式调整离线参数。"""
    scenario_id = 'success_offline' if offline else 'success_online'
    scenario = followup_surface.scenario_info(ENTRY_ID, scenario_id)
    return [
        _apply_basic_command_variants(item, offline=offline, image_archive_path=image_archive_path)
        for item in followup_surface.list_str(scenario, 'commands')
    ]


def _dedupe(items: list[str]) -> list[str]:
    """保持原顺序去重字符串列表。"""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _duration_seconds(check: dict[str, Any]) -> int | None:
    """从检查项 duration 字段解析秒级耗时。"""
    value = check.get('duration_seconds')
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _slow_checks(checks: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    """提取耗时最高的检查项，用于文本摘要展示。"""
    timed: list[dict[str, Any]] = []
    for check in checks:
        duration = _duration_seconds(check)
        if duration is None:
            continue
        timed.append({
            'id': check.get('id'),
            'group': check.get('group'),
            'status': check.get('status'),
            'duration_seconds': duration,
        })
    return sorted(timed, key=lambda item: item['duration_seconds'], reverse=True)[:limit]


def _build_checks(options: dict[str, Any]) -> list[dict[str, Any]]:
    """把 result lines 或 preflight 失败参数转换为标准检查项列表。"""
    checks = [parse_result_line(line) for line in read_lines(str(options.get('result_lines_file') or ''))]
    if checks:
        return checks
    failed_stage = str(options.get('failed_stage') or '').strip()
    if not failed_stage:
        return []
    return [{
        'status': 'FAIL',
        'id': failed_stage,
        'detail': str(options.get('failure_detail') or PREFLIGHT_DETAIL).strip(),
        'group': 'preflight',
    }]


def build_summary(options: dict[str, Any]) -> dict[str, Any]:
    """构建 basic gate 人类摘要和机器摘要的共享数据结构。"""
    checks = _build_checks(options)
    blocking_checks = [item['id'] for item in checks if item.get('status') == 'FAIL']
    warning_checks = [item['id'] for item in checks if item.get('status') == 'WARN']
    skipped_checks = [item['id'] for item in checks if item.get('status') == 'SKIP']
    duration_seconds_total = sum(_duration_seconds(item) or 0 for item in checks)
    offline = bool(options.get('offline'))
    image_archive_path = str(options.get('image_archive_path') or '').strip()
    failure_scenarios: list[str] = []
    if not checks or (checks and checks[0].get('group') == 'preflight' and len(checks) == 1 and not options.get('result_lines_file')):
        failure_scenarios = ['preflight_failed']
    elif int(options.get('return_code') or 0) != 0:
        failure_scenarios = _collect_failure_scenarios(checks)
    next_actions = _success_commands(offline=offline, image_archive_path=image_archive_path)
    if failure_scenarios:
        next_actions = []
        for scenario_id in failure_scenarios:
            next_actions.extend(_scenario_commands(ENTRY_ID, scenario_id, offline=offline, image_archive_path=image_archive_path))
        next_actions = _dedupe(next_actions)
    bucket = {
        'scenario_id': '',
        'scenario_title': '',
        'when': '',
        'doc_path': '',
    }
    if failure_scenarios:
        info = failure_surface.scenario_info(ENTRY_ID, failure_scenarios[0])
        bucket = {
            'scenario_id': failure_scenarios[0],
            'scenario_title': str(info.get('title') or failure_scenarios[0]).strip(),
            'when': str(info.get('when') or '').strip(),
            'doc_path': str(failure_surface.load_config().get('generated_artifacts', {}).get('setup_failure_doc') or '').strip(),
        }
    return {
        'suite': ENTRY_ID,
        'generated_at': str(options.get('generated_at') or utc_now_iso()),
        'env_file': str(options.get('env_file') or ''),
        'mode': {
            'offline': offline,
            'release_check': bool(options.get('release_check', True)),
            'release_policy': str(options.get('release_policy') or 'relaxed_install'),
        },
        'summary': {
            'pass': sum(1 for item in checks if item.get('status') == 'PASS'),
            'fail': len(blocking_checks),
            'warn': len(warning_checks),
            'skip': len(skipped_checks),
            'duration_seconds_total': duration_seconds_total,
        },
        'checks': checks,
        'blocking_checks': blocking_checks,
        'warning_checks': warning_checks,
        'skipped_checks': skipped_checks,
        'slow_checks': _slow_checks(checks),
        'next_actions': next_actions,
        'failed_stage': {
            'id': str(options.get('failed_stage') or (checks[0]['id'] if len(checks) == 1 and checks[0].get('group') == 'preflight' else '')),
            'group': 'preflight' if failure_scenarios == ['preflight_failed'] else '',
        },
        'setup_failure_bucket': bucket,
        'generator': {
            'mode': 'python_surface',
            'reason': 'basic_summary_control_plane',
        },
        'return_code': int(options.get('return_code') or 0),
    }


def build_gate_proof(options: dict[str, Any]) -> dict[str, Any]:
    """构建 latest proof，绑定 env、模式、归档和镜像输入。"""
    return_code = int(options.get('return_code') or 0)
    if return_code != 0:
        fail('basic gate 未通过，不写入成功 proof', 2)
    env_file = Path(str(options.get('env_file') or '')).resolve()
    checks = _build_checks(options)
    blocking_checks = [item['id'] for item in checks if item.get('status') == 'FAIL']
    if blocking_checks:
        fail(f'basic gate 存在失败项，不写入成功 proof：{", ".join(blocking_checks)}', 2)
    image_archive_path = resolve_image_archive_for_mode(options)
    return {
        'schema_version': PROOF_SCHEMA_VERSION,
        'suite': ENTRY_ID,
        'status': 'passed',
        'generated_at': str(options.get('generated_at') or utc_now_iso()),
        'env_file': str(env_file),
        'env_sha256': sha256_file(env_file),
        'mode': {
            'offline': bool(options.get('offline')),
            'release_check': bool(options.get('release_check', True)),
            'release_policy': str(options.get('release_policy') or 'relaxed_install'),
        },
        'image_archive_path': image_archive_path,
        'image_refs': env_image_refs(env_file),
        'summary': {
            'pass': sum(1 for item in checks if item.get('status') == 'PASS'),
            'warn': sum(1 for item in checks if item.get('status') == 'WARN'),
            'skip': sum(1 for item in checks if item.get('status') == 'SKIP'),
            'duration_seconds_total': sum(_duration_seconds(item) or 0 for item in checks),
        },
        'return_code': return_code,
    }


def write_gate_proof(options: dict[str, Any]) -> int:
    """写出 basic gate proof 文件并打印 proof 路径。"""
    proof = build_gate_proof(options)
    proof_path = gate_proof_path(options)
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        proof_path.chmod(0o600)
    except OSError:
        pass
    sys.stdout.write(f'[basic_summary_control_plane] proof={proof_path}\n')
    return 0


def quote_arg(value: str) -> str:
    """为复跑命令中的单个参数做 shell quoting。"""
    return shlex.quote(str(value))


def rerun_basic_gate_command(options: dict[str, Any]) -> str:
    """生成与当前 proof key 输入一致的 basic gate 复跑命令。"""
    parts = ['bash', './scripts/setup/one_click_test_basic.sh']
    env_file = resolve_existing_or_requested_path(str(options.get('env_file') or ''))
    default_env = str((REPO_ROOT / 'deploy' / '.env').resolve())
    if env_file and env_file != default_env:
        parts.extend(['--env-file', env_file])
    if bool(options.get('offline')):
        parts.append('--offline')
    if not bool(options.get('release_check', True)):
        parts.append('--skip-release-check')
    elif str(options.get('release_policy') or '') == 'strict_release':
        parts.append('--strict-release-check')
    image_archive_path = resolve_existing_or_requested_path(str(options.get('image_archive_path') or ''))
    if image_archive_path:
        parts.extend(['--image-archive', image_archive_path])
    return ' '.join(quote_arg(item) for item in parts)


def verify_gate_proof(options: dict[str, Any]) -> int:
    """校验 latest proof 是否仍匹配当前 deploy 输入和运行模式。"""
    proof_path = gate_proof_path(options)
    if not proof_path.is_file():
        fail(f'缺少 basic gate proof：{proof_path}\n请先执行：{rerun_basic_gate_command(options)}', 2)
    try:
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        fail(f'basic gate proof 不是合法 JSON：{proof_path} ({exc})\n请重新执行：{rerun_basic_gate_command(options)}', 2)
    env_file = Path(str(options.get('env_file') or '')).resolve()
    expected = {
        'schema_version': PROOF_SCHEMA_VERSION,
        'suite': ENTRY_ID,
        'status': 'passed',
        'env_file': str(env_file),
        'env_sha256': sha256_file(env_file),
        'offline': bool(options.get('offline')),
        'release_check': bool(options.get('release_check', True)),
        'release_policy': str(options.get('release_policy') or 'relaxed_install'),
        'image_archive_path': resolve_image_archive_for_mode(options),
        'image_refs': env_image_refs(env_file),
    }
    issues: list[str] = []
    if proof.get('schema_version') != expected['schema_version']:
        issues.append('schema_version 不匹配')
    if proof.get('suite') != expected['suite'] or proof.get('status') != expected['status']:
        issues.append('proof 不是 one_click_test_basic passed 状态')
    if str(proof.get('env_file') or '') != expected['env_file']:
        issues.append(f"env_file 不匹配：proof={proof.get('env_file') or '<empty>'} current={expected['env_file']}")
    if str(proof.get('env_sha256') or '') != expected['env_sha256']:
        issues.append('env 内容摘要不匹配')
    mode = proof.get('mode') if isinstance(proof.get('mode'), dict) else {}
    if bool(mode.get('offline')) != expected['offline']:
        issues.append(f"offline 模式不匹配：proof={bool(mode.get('offline'))} current={expected['offline']}")
    if bool(mode.get('release_check', True)) != expected['release_check']:
        issues.append(f"release_check 模式不匹配：proof={bool(mode.get('release_check', True))} current={expected['release_check']}")
    if str(mode.get('release_policy') or 'relaxed_install') != expected['release_policy']:
        issues.append(f"release_policy 不匹配：proof={mode.get('release_policy') or '<empty>'} current={expected['release_policy']}")
    if str(proof.get('image_archive_path') or '') != expected['image_archive_path']:
        issues.append(f"image_archive_path 不匹配：proof={proof.get('image_archive_path') or '<empty>'} current={expected['image_archive_path'] or '<empty>'}")
    if dict(proof.get('image_refs') or {}) != expected['image_refs']:
        issues.append('关键镜像引用不匹配')
    if issues:
        detail = '\n'.join(f'- {item}' for item in issues)
        fail(f'basic gate proof 与当前部署输入不匹配：\n{detail}\n请重新执行：{rerun_basic_gate_command(options)}', 2)
    sys.stdout.write(f'[basic_summary_control_plane] basic gate proof verified: {proof_path}\n')
    return 0


def render_text(summary: dict[str, Any]) -> str:
    """把机器摘要渲染为终端可读的中文文本。"""
    lines = [
        '=== one_click_test_basic 汇总 ===',
        f"PASS: {summary['summary']['pass']}",
        f"FAIL: {summary['summary']['fail']}",
        f"WARN: {summary['summary']['warn']}",
        f"SKIP: {summary['summary']['skip']}",
        f"RECORDED_DURATION_SECONDS: {summary['summary'].get('duration_seconds_total') or 0}",
    ]
    if summary.get('slow_checks'):
        lines.extend(['', 'slow checks:'])
        for item in summary['slow_checks']:
            lines.append(f"- {item.get('id')}: {item.get('duration_seconds')}s status={item.get('status')} group={item.get('group')}")
    if summary['checks']:
        for check in summary['checks']:
            group_suffix = f" ({check['group']})" if check.get('group') else ''
            duration = _duration_seconds(check)
            duration_suffix = f" duration={duration}s" if duration is not None else ''
            lines.extend(['', f"[{check['status']}] {check['id']}{group_suffix}{duration_suffix}"])
            if check.get('detail'):
                lines.append(f"[detail] {check['detail']}")
    if summary['blocking_checks']:
        lines.extend(['', '失败项:'])
        lines.extend([f'- {item}' for item in summary['blocking_checks']])
    if summary['warning_checks']:
        lines.extend(['', '警告项:'])
        lines.extend([f'- {item}' for item in summary['warning_checks']])
    if summary['skipped_checks']:
        lines.extend(['', '跳过项:'])
        lines.extend([f'- {item}' for item in summary['skipped_checks']])
    if summary['setup_failure_bucket'].get('scenario_title'):
        lines.extend([
            '',
            f"[detail] setup 主链失败分流：{summary['setup_failure_bucket']['scenario_title']} ({summary['setup_failure_bucket']['scenario_id']})",
        ])
        if summary['setup_failure_bucket'].get('when'):
            lines.append(f"[detail] 适用条件：{summary['setup_failure_bucket']['when']}")
    if summary['next_actions']:
        lines.extend(['', '下一步动作:'])
        lines.extend([f'{index + 1}. {item}' for index, item in enumerate(summary['next_actions'])])
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：按命令写 proof、验 proof 或输出摘要。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        fail('缺少命令')
    command = args.pop(0)
    options = parse_args(args)
    if command == 'summary':
        summary = build_summary(options)
        if options['format'] == 'json':
            sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        else:
            sys.stdout.write(render_text(summary))
        return 0
    if command == 'write-proof':
        return write_gate_proof(options)
    if command == 'verify-proof':
        return verify_gate_proof(options)
    if command == 'proof-path':
        sys.stdout.write(str(gate_proof_path(options)) + '\n')
        return 0
    fail(f'未知命令：{command}')


if __name__ == '__main__':
    raise SystemExit(main())
