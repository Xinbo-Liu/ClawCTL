from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Callable

from openclaw.doctor.platform.ingress_boundary.normalization import (
    expected_ip_families,
    make_source_port_proofs,
    normalize_cidrs,
)

CommandRunner = Callable[..., tuple[int, str, str]]


def line_matches_tcp_port(line: str, port: int) -> bool:
    if str(port) not in line:
        return False
    if 'tcp' not in line and 'protocol="tcp"' not in line and '-p tcp' not in line:
        return False
    if 'dport' not in line and '--dport' not in line and 'port port=' not in line:
        return False
    return True


def line_matches_service(line: str, port: int) -> bool:
    if port == 80 and 'service name="http"' in line:
        return True
    if port == 443 and 'service name="https"' in line:
        return True
    return False


def parse_firewalld_zone(text: str) -> dict[str, Any]:
    result = {
        'target': '',
        'services': [],
        'ports': [],
        'sources': [],
        'raw': text,
    }
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    for line in lines[1:]:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        if key == 'target':
            result['target'] = value
        elif key == 'services':
            result['services'] = value.split() if value else []
        elif key == 'ports':
            result['ports'] = value.split() if value else []
        elif key == 'sources':
            result['sources'] = value.split() if value else []
    return result


def evaluate_firewalld(
    *,
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_cidrs: list[str],
    required_ports: list[int],
    expected_ip_family_rows: list[str],
    command_runner: CommandRunner,
) -> dict[str, Any] | None:
    rc, _, _ = command_runner('firewall-cmd', '--state')
    if rc != 0:
        return None
    _, default_zone, _ = command_runner('firewall-cmd', '--get-default-zone')
    _, active_zones_raw, _ = command_runner('firewall-cmd', '--get-active-zones')
    _, zones_raw, _ = command_runner('firewall-cmd', '--get-zones')
    _, zones_dump, _ = command_runner('firewall-cmd', '--list-all-zones')
    zones = [item for item in zones_raw.split() if item]
    zone_details: dict[str, dict[str, Any]] = {}
    for zone in zones:
        _, zone_all, _ = command_runner('firewall-cmd', '--zone', zone, '--list-all')
        _, zone_rich, _ = command_runner('firewall-cmd', '--zone', zone, '--list-rich-rules')
        detail = parse_firewalld_zone(zone_all)
        detail['rich_rules'] = [line.strip() for line in zone_rich.splitlines() if line.strip()]
        zone_details[zone] = detail

    source_port_proofs = make_source_port_proofs(allowed_cidrs)
    issues: list[str] = []
    default_deny_ok = False
    for cidr_idx, cidr in enumerate(allowed_cidrs):
        for port in required_ports:
            proofs: list[str] = []
            for zone_name, detail in zone_details.items():
                zone_allows = False
                if cidr in detail.get('sources', []):
                    if f'{port}/tcp' in detail.get('ports', []):
                        zone_allows = True
                    if port == 80 and 'http' in detail.get('services', []):
                        zone_allows = True
                    if port == 443 and 'https' in detail.get('services', []):
                        zone_allows = True
                if zone_allows:
                    proofs.append(f'zone:{zone_name}')
                for rich_rule in detail.get('rich_rules', []):
                    if cidr in rich_rule and 'accept' in rich_rule and (
                        line_matches_tcp_port(rich_rule, port) or line_matches_service(rich_rule, port)
                    ):
                        proofs.append(f'rich_rule:{zone_name}')
                    if ('drop' in rich_rule or 'reject' in rich_rule) and (
                        line_matches_tcp_port(rich_rule, port) or line_matches_service(rich_rule, port)
                    ):
                        default_deny_ok = True
                target = str(detail.get('target') or '').upper()
                if target in {'DROP', 'REJECT', '%%REJECT%%'}:
                    default_deny_ok = True
            source_port_proofs[cidr_idx]['ports'].append({
                'port': port,
                'proved': bool(proofs),
                'proofs': proofs,
            })
            if not proofs:
                issues.append(f'firewalld 未能证明 {cidr} 可访问 {port}/tcp')
    if not default_deny_ok:
        issues.append('firewalld 未能证明 80/443 对非授权来源默认拒绝')
    return {
        'mode': mode,
        'method': 'firewalld',
        'accepted': not issues,
        'semantic_contract_ok': not issues,
        'expected_bind_ip': expected_ip,
        'expected_tls_cn': expected_host,
        'allowed_source_cidrs': allowed_cidrs,
        'required_host_ports': required_ports,
        'required_ip_families': expected_ip_family_rows,
        'default_deny_ok': default_deny_ok,
        'source_port_proofs': source_port_proofs,
        'default_zone': default_zone,
        'active_zones': active_zones_raw,
        'zones': zone_details,
        'snapshot': zones_dump,
        'issues': issues,
    }


def evaluate_nftables(
    *,
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_cidrs: list[str],
    required_ports: list[int],
    expected_ip_family_rows: list[str],
    command_runner: CommandRunner,
) -> dict[str, Any] | None:
    rc, ruleset, _ = command_runner('nft', 'list', 'ruleset')
    if rc != 0 or not ruleset:
        return None
    source_port_proofs = make_source_port_proofs(allowed_cidrs)
    issues: list[str] = []
    policy_drop = bool(
        re.search(r'chain\s+\S+\s*\{[^}]*hook\s+input\b[^}]*policy\s+(drop|reject)\b', ruleset, flags=re.IGNORECASE | re.DOTALL)
    )
    deny_lines = [
        line.strip()
        for line in ruleset.splitlines()
        if line.strip() and ('drop' in line or 'reject' in line) and any(line_matches_tcp_port(line, port) for port in required_ports)
    ]
    default_deny_ok = policy_drop or bool(deny_lines)
    lines = [line.strip() for line in ruleset.splitlines() if line.strip()]
    for cidr_idx, cidr in enumerate(allowed_cidrs):
        for port in required_ports:
            proofs = [line for line in lines if cidr in line and 'accept' in line and line_matches_tcp_port(line, port)]
            source_port_proofs[cidr_idx]['ports'].append({
                'port': port,
                'proved': bool(proofs),
                'proofs': proofs,
            })
            if not proofs:
                issues.append(f'nftables 未能证明 {cidr} 可访问 {port}/tcp')
    if not default_deny_ok:
        issues.append('nftables 未能证明 80/443 对非授权来源默认拒绝')
    return {
        'mode': mode,
        'method': 'nftables',
        'accepted': not issues,
        'semantic_contract_ok': not issues,
        'expected_bind_ip': expected_ip,
        'expected_tls_cn': expected_host,
        'allowed_source_cidrs': allowed_cidrs,
        'required_host_ports': required_ports,
        'required_ip_families': expected_ip_family_rows,
        'default_deny_ok': default_deny_ok,
        'source_port_proofs': source_port_proofs,
        'policy_drop': policy_drop,
        'deny_lines': deny_lines,
        'snapshot': ruleset,
        'issues': issues,
    }


def evaluate_iptables_family(
    command_name: str,
    family: str,
    family_cidrs: list[str],
    *,
    expected_ip: str,
    required_ports: list[int],
    command_runner: CommandRunner,
) -> dict[str, Any] | None:
    rc, snapshot, _ = command_runner(command_name)
    if rc != 0 or not snapshot:
        return None
    source_port_proofs = [{'source_cidr': cidr, 'ports': []} for cidr in family_cidrs]
    issues: list[str] = []
    lines = [line.strip() for line in snapshot.splitlines() if line.strip()]
    input_policy_drop = bool(re.search(r'^:INPUT\s+(DROP|REJECT)\b', snapshot, flags=re.MULTILINE))
    expected_family = 'ipv6' if ipaddress.ip_address(expected_ip).version == 6 else 'ipv4'

    def requires_ctorigdst() -> bool:
        return family == expected_family

    def line_matches_ctorigdstport(line: str, port: int) -> bool:
        return '-m conntrack' in line and f'--ctorigdstport {port}' in line

    def line_matches_ctorigdst(line: str) -> bool:
        if not requires_ctorigdst():
            return True
        return f'--ctorigdst {expected_ip}' in line

    docker_user_denied_ports: set[int] = set()
    docker_user_established_ports: set[int] = set()
    for line in lines:
        if not line.startswith('-A DOCKER-USER '):
            continue
        for port in required_ports:
            if (
                '-j ACCEPT' in line
                and '-p tcp' in line
                and '--ctstate' in line
                and 'ESTABLISHED' in line
                and line_matches_ctorigdstport(line, port)
                and line_matches_ctorigdst(line)
            ):
                docker_user_established_ports.add(port)
            if '-j DROP' not in line and '-j REJECT' not in line:
                continue
            if line_matches_ctorigdstport(line, port) and line_matches_ctorigdst(line) and '-p tcp' in line:
                docker_user_denied_ports.add(port)

    default_deny_ok = set(required_ports).issubset(docker_user_denied_ports)
    established_return_ok = set(required_ports).issubset(docker_user_established_ports)
    if input_policy_drop and not default_deny_ok:
        issues.append(
            f'iptables[{family}] 检测到 INPUT 默认拒绝，但 Docker 发布端口仍必须由 DOCKER-USER 明确证明 80/443 对非授权来源默认拒绝'
        )
    if not established_return_ok:
        issues.append(f'iptables[{family}] 未能证明 DOCKER-USER 允许 80/443 的 RELATED,ESTABLISHED 返回流量')

    for cidr_idx, cidr in enumerate(family_cidrs):
        for port in required_ports:
            docker_user_proofs: list[str] = []
            input_proofs: list[str] = []
            for line in lines:
                if '-j ACCEPT' not in line or '-p tcp' not in line:
                    continue
                if f'-s {cidr}' not in line:
                    continue
                if line.startswith('-A DOCKER-USER '):
                    if not line_matches_ctorigdstport(line, port):
                        continue
                    if not line_matches_ctorigdst(line):
                        continue
                    docker_user_proofs.append(line)
                elif line.startswith('-A INPUT '):
                    if f'--dport {port}' not in line:
                        continue
                    if '-d ' in line and f'-d {expected_ip}' not in line:
                        continue
                    input_proofs.append(line)
            proofs = docker_user_proofs + input_proofs
            proved = bool(docker_user_proofs)
            source_port_proofs[cidr_idx]['ports'].append({
                'port': port,
                'proved': proved,
                'proofs': proofs,
                'docker_user_proofs': docker_user_proofs,
                'input_proofs': input_proofs,
            })
            if input_proofs and not docker_user_proofs:
                issues.append(f'iptables[{family}] 仅在 INPUT 链发现 {cidr} 可访问 {port}/tcp；Docker 发布端口必须由 DOCKER-USER 明确覆盖')
            if not docker_user_proofs:
                issues.append(f'iptables[{family}] 未能通过 DOCKER-USER 以 conntrack 原始目的地址/端口证明 {cidr} 可访问 {port}/tcp')

    if not default_deny_ok:
        issues.append(f'iptables[{family}] 未能通过 DOCKER-USER 以 conntrack 原始目的地址/端口证明 80/443 对非授权来源默认拒绝')

    return {
        'family': family,
        'command': command_name,
        'default_deny_ok': default_deny_ok,
        'source_port_proofs': source_port_proofs,
        'input_policy_drop': input_policy_drop,
        'docker_user_denied_ports': sorted(docker_user_denied_ports),
        'docker_user_established_ports': sorted(docker_user_established_ports),
        'established_return_ok': established_return_ok,
        'ctorigdst_required': requires_ctorigdst(),
        'snapshot': snapshot,
        'issues': issues,
    }


def evaluate_iptables(
    *,
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_cidrs: list[str],
    required_ports: list[int],
    expected_ip_family_rows: list[str],
    command_runner: CommandRunner,
) -> dict[str, Any] | None:
    family_rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for family in expected_ip_family_rows:
        command_name = 'ip6tables-save' if family == 'ipv6' else 'iptables-save'
        family_cidrs = [
            cidr
            for cidr in allowed_cidrs
            if ipaddress.ip_network(cidr, strict=False).version == (6 if family == 'ipv6' else 4)
        ]
        row = evaluate_iptables_family(
            command_name,
            family,
            family_cidrs,
            expected_ip=expected_ip,
            required_ports=required_ports,
            command_runner=command_runner,
        )
        if row is None:
            issues.append(f'未发现可用于 {family} 语义校验的 {command_name}')
            continue
        family_rows.append(row)
        issues.extend(list(row.get('issues') or []))
    if not family_rows:
        return None
    default_deny_ok = all(bool(row.get('default_deny_ok')) for row in family_rows)
    return {
        'mode': mode,
        'method': 'iptables',
        'accepted': not issues,
        'semantic_contract_ok': not issues,
        'expected_bind_ip': expected_ip,
        'expected_tls_cn': expected_host,
        'allowed_source_cidrs': allowed_cidrs,
        'required_host_ports': required_ports,
        'required_ip_families': expected_ip_family_rows,
        'default_deny_ok': default_deny_ok,
        'family_evidence': family_rows,
        'issues': issues,
    }


def evaluate_external_acl(
    *,
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_cidrs: list[str],
    required_ports: list[int],
    expected_ip_family_rows: list[str],
    policy: dict[str, Any],
    evidence_path: str,
) -> dict[str, Any]:
    if not evidence_path:
        return {
            'mode': mode,
            'method': 'external_acl',
            'accepted': False,
            'semantic_contract_ok': False,
            'path': '',
            'issues': ['OPENCLAW_INGRESS_BOUNDARY_EVIDENCE_PATH 为空'],
        }
    path = Path(evidence_path)
    if not path.exists() or path.stat().st_size <= 0:
        return {
            'mode': mode,
            'method': 'external_acl',
            'accepted': False,
            'semantic_contract_ok': False,
            'path': str(path),
            'issues': ['证据文件不存在或为空'],
        }
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {
            'mode': mode,
            'method': 'external_acl',
            'accepted': False,
            'semantic_contract_ok': False,
            'path': str(path),
            'issues': [f'证据文件不是合法 JSON：{exc}'],
        }

    issues: list[str] = []
    contract = policy.get('external_acl_contract') or {}
    required_keys = list(contract.get('required_keys') or [])
    for key in required_keys:
        if key not in payload:
            issues.append(f'外部 ACL 证据缺少键：{key}')

    target_keys_any_of = list(contract.get('target_keys_any_of') or [])
    if target_keys_any_of and not any(payload.get(key) for key in target_keys_any_of):
        issues.append('外部 ACL 证据缺少目标信息：target_bind_ip 或 target_hostnames 至少提供一项')

    normalized_sources: list[str] = []
    try:
        normalized_sources = normalize_cidrs(payload.get('source_cidrs') or [])
    except Exception as exc:
        issues.append(f'外部 ACL 证据中的 source_cidrs 非法：{exc}')
    if normalized_sources and normalized_sources != allowed_cidrs:
        if set(normalized_sources) != set(allowed_cidrs):
            issues.append(f'外部 ACL 证据中的 source_cidrs 与部署输入不一致：expected={allowed_cidrs} actual={normalized_sources}')
        else:
            normalized_sources = sorted(normalized_sources)

    allowed_ports: list[int] = []
    try:
        allowed_ports = sorted(int(item) for item in list(payload.get('allowed_ports') or []))
    except Exception:
        issues.append('外部 ACL 证据中的 allowed_ports 必须为整数列表')
    if allowed_ports and allowed_ports != sorted(required_ports):
        if set(allowed_ports) != set(required_ports):
            issues.append(f'外部 ACL 证据中的 allowed_ports 与部署输入不一致：expected={sorted(required_ports)} actual={allowed_ports}')
        elif len(allowed_ports) != len(required_ports):
            issues.append(f'外部 ACL 证据中的 allowed_ports 不允许重复：actual={allowed_ports}')

    default_deny = payload.get('default_deny')
    if contract.get('default_deny_required') and default_deny is not True:
        issues.append('外部 ACL 证据中的 default_deny 必须显式为 true')

    ip_families = sorted({str(item).strip().lower() for item in list(payload.get('ip_families') or []) if str(item).strip()})
    if not ip_families:
        issues.append('外部 ACL 证据中的 ip_families 不能为空')
    elif set(ip_families) != set(expected_ip_family_rows):
        issues.append(f'外部 ACL 证据中的 ip_families 与部署输入不一致：expected={expected_ip_family_rows} actual={ip_families}')

    enforcement_plane = str(payload.get('enforcement_plane') or '').strip()
    if not enforcement_plane:
        issues.append('外部 ACL 证据中的 enforcement_plane 不能为空')

    target_bind_ip = str(payload.get('target_bind_ip') or '').strip()
    if target_bind_ip and target_bind_ip != expected_ip:
        issues.append(f'外部 ACL 证据中的 target_bind_ip 与当前部署输入不一致：expected={expected_ip} actual={target_bind_ip}')
    target_hostnames = [str(item).strip() for item in list(payload.get('target_hostnames') or []) if str(item).strip()]
    if target_hostnames and expected_host not in target_hostnames:
        issues.append(f'外部 ACL 证据中的 target_hostnames 未覆盖当前 OPENCLAW_TLS_CN：expected={expected_host} actual={target_hostnames}')

    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        'mode': mode,
        'method': 'external_acl',
        'accepted': not issues,
        'semantic_contract_ok': not issues,
        'path': str(path),
        'sha256': sha256,
        'size_bytes': path.stat().st_size,
        'expected_bind_ip': expected_ip,
        'expected_tls_cn': expected_host,
        'allowed_source_cidrs': allowed_cidrs,
        'required_host_ports': required_ports,
        'required_ip_families': expected_ip_family_rows,
        'evidence': payload,
        'issues': issues,
    }
