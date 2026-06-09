from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

RFC1918_V4_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
LOOPBACK_V4_NETWORK = ipaddress.IPv4Network("127.0.0.0/8")
ULA_V6_NETWORK = ipaddress.IPv6Network("fc00::/7")
LOOPBACK_V6_NETWORK = ipaddress.IPv6Network("::1/128")


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump_payload(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def normalize_source_cidrs(raw: str) -> tuple[dict[str, Any], int]:
    parts = [item.strip() for item in str(raw or '').split(',') if item.strip()]
    issues: list[str] = []
    normalized: list[str] = []
    ipv4: list[str] = []
    ipv6: list[str] = []
    seen: set[str] = set()
    if not parts:
        issues.append('OPENCLAW_INGRESS_ALLOWED_SOURCE_CIDRS 必须提供至少一个 CIDR')
    for item in parts:
        try:
            network = ipaddress.ip_network(item, strict=False)
        except Exception:
            issues.append(f'无效 CIDR：{item}')
            continue
        if network.version == 4:
            allowed = any(network.subnet_of(base) or network == base for base in RFC1918_V4_NETWORKS) or network.subnet_of(LOOPBACK_V4_NETWORK)
        else:
            allowed = network.subnet_of(ULA_V6_NETWORK) or network.subnet_of(LOOPBACK_V6_NETWORK)
        if not allowed:
            issues.append(f'只允许私网或 loopback CIDR：{item}')
            continue
        text = network.with_prefixlen
        if text in seen:
            issues.append(f'CIDR 重复：{text}')
            continue
        seen.add(text)
        normalized.append(text)
        if network.version == 4:
            ipv4.append(text)
        else:
            ipv6.append(text)
    payload = {
        'schema_version': 1,
        'accepted': not issues,
        'source_cidrs': normalized,
        'ipv4': ipv4,
        'ipv6': ipv6,
        'issues': issues,
    }
    return payload, 0 if not issues else 2


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_port_entry(raw: Any) -> dict[str, Any]:
    result = {
        'raw': raw,
        'host_ip': '',
        'published': None,
        'target': None,
        'protocol': 'tcp',
    }
    if isinstance(raw, dict):
        result['host_ip'] = str(raw.get('host_ip') or '')
        if raw.get('published') is not None:
            result['published'] = int(str(raw.get('published')))
        if raw.get('target') is not None:
            result['target'] = int(str(raw.get('target')))
        result['protocol'] = str(raw.get('protocol') or 'tcp')
        return result
    text = str(raw)
    protocol = 'tcp'
    if '/' in text:
        text, protocol = text.rsplit('/', 1)
    if text.startswith('[') and ']' in text:
        end = text.index(']')
        host_ip = text[1:end]
        remainder = text[end + 1 :]
        if remainder.startswith(':'):
            remainder = remainder[1:]
        parts = remainder.split(':')
    else:
        parts = text.rsplit(':', 2)
        host_ip = ''
    if len(parts) == 3:
        host_ip, published, target = parts
    elif len(parts) == 2:
        published, target = parts
    else:
        host_ip = ''
        published = parts[0]
        target = parts[0]
    result['host_ip'] = host_ip
    try:
        result['published'] = int(str(published))
    except Exception:
        result['published'] = None
    try:
        result['target'] = int(str(target))
    except Exception:
        result['target'] = None
    result['protocol'] = protocol or 'tcp'
    return result


def normalize_cidrs(values: list[Any] | None) -> list[str]:
    rows: list[str] = []
    for item in list(values or []):
        network = ipaddress.ip_network(str(item), strict=False)
        rows.append(network.with_prefixlen)
    return rows


def expected_ip_families(expected_ip: str, allowed_cidrs: list[str]) -> list[str]:
    families = {'ipv6' if ipaddress.ip_address(expected_ip).version == 6 else 'ipv4'}
    for cidr in allowed_cidrs:
        families.add('ipv6' if ipaddress.ip_network(cidr, strict=False).version == 6 else 'ipv4')
    return sorted(families)


def make_source_port_proofs(allowed_cidrs: list[str]) -> list[dict[str, Any]]:
    return [{'source_cidr': cidr, 'ports': []} for cidr in allowed_cidrs]


def port_bindings_from_inspect(items: list[Any] | None) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for row in items or []:
        if not row:
            continue
        ports = (((row or {}).get('HostConfig') or {}).get('PortBindings') or {})
        for container_port, host_rows in ports.items():
            protocol = 'tcp'
            target = None
            if '/' in container_port:
                target_text, protocol = container_port.split('/', 1)
                try:
                    target = int(target_text)
                except Exception:
                    target = None
            for host_row in host_rows or []:
                published = host_row.get('HostPort')
                try:
                    published_int = int(str(published)) if published is not None else None
                except Exception:
                    published_int = None
                bindings.append({
                    'host_ip': str(host_row.get('HostIp') or ''),
                    'published': published_int,
                    'target': target,
                    'protocol': protocol or 'tcp',
                })
    return bindings
