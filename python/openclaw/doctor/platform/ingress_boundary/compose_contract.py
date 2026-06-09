from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.doctor.platform.ingress_boundary.normalization import ensure_list, load_json, parse_port_entry


def _parse_scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _minimal_compose_yaml_load(text: str) -> dict[str, Any]:
    services: dict[str, dict[str, Any]] = {}
    current_service = ''
    current_port: dict[str, Any] | None = None
    in_services = False
    in_ports = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith('#'):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(' '))
        line = raw_line.strip()
        if indent == 0:
            in_services = line == 'services:'
            current_service = ''
            current_port = None
            in_ports = False
            continue
        if not in_services:
            continue
        if indent == 2 and line.endswith(':'):
            current_service = line[:-1].strip()
            services.setdefault(current_service, {})
            current_port = None
            in_ports = False
            continue
        if not current_service:
            continue
        if indent == 4:
            in_ports = line == 'ports:'
            current_port = None
            if in_ports:
                services[current_service].setdefault('ports', [])
            continue
        if not in_ports:
            continue
        ports = services[current_service].setdefault('ports', [])
        if indent == 6 and line.startswith('- '):
            payload = line[2:].strip()
            if not payload:
                current_port = {}
                ports.append(current_port)
                continue
            if ': ' not in payload:
                ports.append(_parse_scalar(payload))
                current_port = None
                continue
            key, value = payload.split(':', 1)
            current_port = {key.strip(): _parse_scalar(value)}
            ports.append(current_port)
            continue
        if current_port is not None and indent >= 8 and ':' in line:
            key, value = line.split(':', 1)
            current_port[key.strip()] = _parse_scalar(value)
    return {'services': services}


def _load_compose_object(rendered_compose_path: str) -> dict[str, Any]:
    text = Path(rendered_compose_path).read_text(encoding='utf-8')
    if text.lstrip().startswith('{'):
        return json.loads(text) or {}
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return _minimal_compose_yaml_load(text)
    return yaml.safe_load(text) or {}


def compose_contract_summary(rendered_compose_path: str, expected_ip: str, policy_path: str) -> dict[str, Any]:
    policy = load_json(policy_path)
    obj = _load_compose_object(rendered_compose_path)
    services = obj.get('services') or {}
    if not isinstance(services, dict):
        raise SystemExit('[FAIL] compose 渲染结果缺少 services 对象')

    ingress_service = str(policy.get('ingress_service') or 'openclaw-private-ingress')
    required_host_ports = [int(item) for item in list(policy.get('required_host_ports') or [])]
    allowed_published_services = set(str(item) for item in list(policy.get('allowed_published_services') or []))

    service_ports: dict[str, list[dict[str, Any]]] = {}
    non_ingress_with_ports: list[str] = []
    for service_name, service in services.items():
        parsed = [parse_port_entry(item) for item in ensure_list(service.get('ports'))]
        service_ports[service_name] = parsed
        if parsed and service_name not in allowed_published_services:
            non_ingress_with_ports.append(service_name)

    ingress_ports = service_ports.get(ingress_service) or []
    issues: list[str] = []
    required_bindings: list[dict[str, Any]] = []
    for port in required_host_ports:
        matched = [
            item
            for item in ingress_ports
            if item.get('published') == port and item.get('target') == port and item.get('protocol') == 'tcp'
        ]
        required_bindings.append({
            'port': port,
            'matched': bool(matched),
            'bindings': matched,
        })
        if not matched:
            issues.append(f'{ingress_service} 缺少 {port}:{port}/tcp 绑定')
        elif not any((item.get('host_ip') or '') == expected_ip for item in matched):
            issues.append(f'{ingress_service} 的 {port}:{port}/tcp 未绑定到 {expected_ip}')

    extra_ingress_ports = [
        item
        for item in ingress_ports
        if item.get('published') not in required_host_ports or item.get('target') not in required_host_ports
    ]
    if extra_ingress_ports:
        issues.append(f'{ingress_service} 出现额外宿主机端口暴露：{extra_ingress_ports}')
    if non_ingress_with_ports:
        issues.append('非 ingress 服务出现宿主机端口映射：' + ', '.join(sorted(non_ingress_with_ports)))

    return {
        'schema_version': 1,
        'compose_contract_ok': not issues,
        'ingress_service': ingress_service,
        'expected_bind_ip': expected_ip,
        'required_host_ports': required_host_ports,
        'required_bindings': required_bindings,
        'service_ports': service_ports,
        'non_ingress_with_ports': sorted(non_ingress_with_ports),
        'issues': issues,
    }
