"""OpenClaw upstream overlay contract checker."""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from openclaw.lib.repo.layout import resolve_repo_root
from typing import NoReturn


@dataclass(frozen=True)
class OverlayContext:
    root_dir: Path
    contract_path: Path
    official_gateway_image: str


class OverlayContractError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise OverlayContractError(message)


def image_repo(image_ref: str) -> str:
    text = (image_ref or '').strip()
    if not text:
        fail('环境变量 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 为空，无法执行 overlay 合同检查')
    without_digest = text.split('@', 1)[0]
    slash_index = without_digest.rfind('/')
    colon_index = without_digest.rfind(':')
    if colon_index > slash_index:
        return without_digest[:colon_index]
    return without_digest


def load_contract(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise OverlayContractError(f'缺少合同文件：{path}') from exc
    except json.JSONDecodeError as exc:
        raise OverlayContractError(f'合同 JSON 非法：{path} ({exc})') from exc


def require_file(path: Path) -> None:
    if not path.is_file():
        fail(f'缺少文件：{path}')


def as_string_list(payload: object, label: str) -> list[str]:
    if payload is None:
        return []
    items = payload
    if not isinstance(items, list):
        fail(f'{label} 必须是字符串数组')
    string_items = [item for item in items if isinstance(item, str)]
    if len(string_items) != len(items):
        fail(f'{label} 必须是字符串数组')
    return string_items


def as_mapping(payload: object, label: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        fail(f'{label} 必须是对象')
    return {str(key): value for key, value in payload.items()}


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _strip_inline_comment(line: str) -> str:
    if '#' not in line:
        return line.rstrip()
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == '#' and not in_single and not in_double:
            return line[:index].rstrip()
    return line.rstrip()


def _parse_inline_value(value: str) -> object:
    text = _strip_inline_comment(value).strip()
    if not text:
        return ''
    if text.startswith('[') or text.startswith('{'):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        try:
            return shlex.split(text)[0]
        except Exception:
            return text[1:-1]
    return text


def _parse_compose_services(compose_path: Path) -> dict[str, dict[str, object]]:
    lines = compose_path.read_text(encoding='utf-8').splitlines()
    services_line = next((idx for idx, line in enumerate(lines) if line.strip() == 'services:'), None)
    if services_line is None:
        fail(f'{compose_path} 缺少 services 段')
    assert services_line is not None
    services: dict[str, dict[str, object]] = {}
    current_name = ''
    current_key = ''
    for raw_line in lines[services_line + 1:]:
        if not raw_line.strip() or raw_line.lstrip().startswith('#'):
            continue
        indent = _line_indent(raw_line)
        stripped = _strip_inline_comment(raw_line).strip()
        if not stripped:
            continue
        if indent == 0:
            break
        if indent == 2 and stripped.endswith(':'):
            current_name = stripped[:-1]
            services[current_name] = {'_keys': set()}
            current_key = ''
            continue
        if not current_name:
            continue
        service = services[current_name]
        if indent == 4 and ':' in stripped:
            key, value = stripped.split(':', 1)
            current_key = key.strip()
            keys = service.setdefault('_keys', set())
            assert isinstance(keys, set)
            keys.add(current_key)
            parsed = _parse_inline_value(value)
            if parsed != '':
                service[current_key] = parsed
            elif current_key not in service:
                service[current_key] = [] if current_key in {'env_file', 'volumes'} else {}
            continue
        if indent >= 6 and current_key:
            if stripped.startswith('- '):
                items = service.setdefault(current_key, [])
                if isinstance(items, list):
                    items.append(str(_parse_inline_value(stripped[2:])))
                continue
            if ':' in stripped:
                mapping = service.setdefault(current_key, {})
                if isinstance(mapping, dict):
                    key, value = stripped.split(':', 1)
                    mapping[key.strip()] = _parse_inline_value(value)
    return services


def _compose_image_from_env(env_name: str) -> str:
    return f'${{{env_name}:?{env_name}_required}}'


def _check_service_contract(service_name: str, service: dict[str, object], spec: dict[str, object]) -> None:
    image_env = str(spec.get('image_env') or '').strip()
    if image_env:
        expected_image = _compose_image_from_env(image_env)
        if str(service.get('image') or '').strip() != expected_image:
            fail(f'{service_name}.image 应绑定 {expected_image}')
    expected_command = spec.get('command')
    if expected_command is not None and service.get('command') != expected_command:
        fail(f'{service_name}.command 与结构化合同不一致')
    expected_env = as_mapping(spec.get('environment') or {}, f'{service_name}.environment')
    actual_env = service.get('environment') if isinstance(service.get('environment'), dict) else {}
    assert isinstance(actual_env, dict)
    for key, expected_value in expected_env.items():
        if str(actual_env.get(key) or '').strip() != str(expected_value):
            fail(f'{service_name}.environment.{key} 与结构化合同不一致')
    expected_env_files = as_string_list(spec.get('env_file'), f'{service_name}.env_file') if spec.get('env_file') is not None else []
    actual_env_files = [str(item) for item in service.get('env_file', [])] if isinstance(service.get('env_file'), list) else []
    for expected in expected_env_files:
        if expected not in actual_env_files:
            fail(f'{service_name}.env_file 缺少 {expected}')
    expected_volume_targets = as_string_list(spec.get('volume_targets'), f'{service_name}.volume_targets') if spec.get('volume_targets') is not None else []
    actual_volume_rows = [str(item) for item in service.get('volumes', [])] if isinstance(service.get('volumes'), list) else []
    for expected in expected_volume_targets:
        if not any(f':{expected}' in row or row == expected for row in actual_volume_rows):
            fail(f'{service_name}.volumes 缺少目标挂载 {expected}')


def _check_compose_must_not(compose_path: Path, services: dict[str, dict[str, object]], spec: dict[str, object]) -> None:
    section = as_mapping(spec.get('must_not') or {}, 'compose_contract.must_not')
    raw_service_keys = as_string_list(section.get('service_keys'), 'compose_contract.must_not.service_keys') if section.get('service_keys') is not None else []
    for service_name, service in services.items():
        keys = service.get('_keys')
        service_keys = keys if isinstance(keys, set) else set()
        for key in raw_service_keys:
            if key in service_keys:
                fail(f'{service_name} 不应声明 compose key：{key}')
    content = compose_path.read_text(encoding='utf-8')
    for image_ref in as_string_list(section.get('image_refs'), 'compose_contract.must_not.image_refs') if section.get('image_refs') is not None else []:
        if image_ref in content:
            fail(f'{compose_path} 不应固定镜像引用：{image_ref}')
    for fragment in as_string_list(section.get('command_fragments'), 'compose_contract.must_not.command_fragments') if section.get('command_fragments') is not None else []:
        if fragment in content.replace('", "', ' '):
            fail(f'{compose_path} 不应包含命令片段：{fragment}')
    for fragment in as_string_list(section.get('service_name_fragments'), 'compose_contract.must_not.service_name_fragments') if section.get('service_name_fragments') is not None else []:
        for service_name in services:
            if service_name != 'openclaw-control-plane-scheduler' and fragment in service_name:
                fail(f'compose 服务名不应引入额外运行服务片段：{service_name}')


def check_compose(root_dir: Path, section: dict[str, object]) -> None:
    file_rel = section.get('file')
    if not isinstance(file_rel, str) or not file_rel:
        fail('compose_contract.file 必须是非空字符串')
    compose_file: str = file_rel
    file_path = root_dir / compose_file
    require_file(file_path)
    services = _parse_compose_services(file_path)
    service_specs = as_mapping(section.get('services'), 'compose_contract.services')
    for service_name, raw_spec in service_specs.items():
        if service_name not in services:
            fail(f'{file_path} 缺少服务：{service_name}')
        _check_service_contract(service_name, services[service_name], as_mapping(raw_spec, f'compose_contract.services.{service_name}'))
    _check_compose_must_not(file_path, services, section)


def build_context() -> OverlayContext:
    root_dir = resolve_repo_root(Path(__file__))
    contract_path = root_dir / 'config/upstream/overlay_contract.json'
    return OverlayContext(
        root_dir=root_dir,
        contract_path=contract_path,
        official_gateway_image=os.environ.get('OPENCLAW_OFFICIAL_GATEWAY_IMAGE', ''),
    )


def main(argv: list[str] | None = None) -> int:
    _ = argv
    ctx = build_context()
    contract = load_contract(ctx.contract_path)
    allowed_repos = as_string_list(contract.get('allowed_base_image_repositories'), 'allowed_base_image_repositories')
    current_repo = image_repo(ctx.official_gateway_image)
    if current_repo not in allowed_repos:
        fail(f'当前 OPENCLAW_OFFICIAL_GATEWAY_IMAGE 仓库不在允许列表中：{current_repo}')
    check_compose(ctx.root_dir, as_mapping(contract.get('compose_contract'), 'compose_contract'))
    print('[check_openclaw_overlay_contract] overlay contract passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
