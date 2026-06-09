from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from openclaw.doctor.platform.ingress_boundary.firewall import (
    evaluate_external_acl,
    evaluate_firewalld,
    evaluate_iptables,
    evaluate_nftables,
)
from openclaw.doctor.platform.ingress_boundary.normalization import (
    expected_ip_families,
    load_json,
    port_bindings_from_inspect,
)

CommandRunner = Callable[..., tuple[int, str, str]]


def evaluate_boundary_evidence(
    mode: str,
    expected_ip: str,
    expected_host: str,
    allowed_sources_path: str,
    policy_path: str,
    evidence_path: str = '',
    *,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    allowed_sources = load_json(allowed_sources_path)
    policy = load_json(policy_path)
    required_ports = [int(item) for item in list(policy.get('required_host_ports') or [])]
    allowed_cidrs = [str(item) for item in list(allowed_sources.get('source_cidrs') or [])]
    expected_ip_family_rows = expected_ip_families(expected_ip, allowed_cidrs)

    if mode == 'host_firewall':
        candidate_rows: list[dict[str, Any]] = []
        for evaluator in (evaluate_iptables, evaluate_nftables, evaluate_firewalld):
            row = evaluator(
                mode=mode,
                expected_ip=expected_ip,
                expected_host=expected_host,
                allowed_cidrs=allowed_cidrs,
                required_ports=required_ports,
                expected_ip_family_rows=expected_ip_family_rows,
                command_runner=command_runner,
            )
            if row is not None:
                candidate_rows.append(row)
        if not candidate_rows:
            return {
                'mode': mode,
                'method': 'none',
                'accepted': False,
                'semantic_contract_ok': False,
                'expected_bind_ip': expected_ip,
                'expected_tls_cn': expected_host,
                'allowed_source_cidrs': allowed_cidrs,
                'required_host_ports': required_ports,
                'required_ip_families': expected_ip_family_rows,
                'issues': ['未发现可用于语义校验的 firewalld / nftables / iptables'],
            }

        accepted_rows = [row for row in candidate_rows if row.get('accepted') is True]
        preferred = accepted_rows[0] if accepted_rows else candidate_rows[0]
        result = dict(preferred)
        result['evaluated_methods'] = [
            {
                'method': row.get('method'),
                'accepted': row.get('accepted'),
                'issues': row.get('issues') or [],
            }
            for row in candidate_rows
        ]
        if not accepted_rows and len(candidate_rows) > 1:
            merged_issues: list[str] = []
            seen: set[str] = set()
            for row in candidate_rows:
                for item in list(row.get('issues') or []):
                    if item in seen:
                        continue
                    seen.add(item)
                    merged_issues.append(item)
            result['issues'] = merged_issues
        return result

    return evaluate_external_acl(
        mode=mode,
        expected_ip=expected_ip,
        expected_host=expected_host,
        allowed_cidrs=allowed_cidrs,
        required_ports=required_ports,
        expected_ip_family_rows=expected_ip_family_rows,
        policy=policy,
        evidence_path=evidence_path,
    )


def ingress_boundary_summary(
    compose_json_path: str,
    runtime_json_path: str,
    boundary_json_path: str,
    expected_ip: str,
    policy_path: str,
) -> dict[str, Any]:
    compose = load_json(compose_json_path)
    runtime_payload = load_json(runtime_json_path)
    boundary = load_json(boundary_json_path)
    policy = load_json(policy_path)
    required_ports = [int(item) for item in list(policy.get('required_host_ports') or [])]
    ingress_service = str(policy.get('ingress_service') or 'openclaw-private-ingress')

    runtime_services: dict[str, list[dict[str, Any]]] = {}
    runtime_non_ingress_with_ports: list[str] = []
    runtime_issues: list[str] = []
    runtime_present_targets: list[str] = []
    for target, rows in dict(runtime_payload).items():
        bindings = port_bindings_from_inspect(rows)
        runtime_services[str(target)] = bindings
        if rows:
            runtime_present_targets.append(str(target))
        if target != 'ingress' and bindings:
            runtime_non_ingress_with_ports.append(str(target))

    runtime_state = 'containers_absent' if not runtime_present_targets else (
        'partial' if len(runtime_present_targets) < len(runtime_payload) else 'present'
    )
    required_runtime: list[dict[str, Any]] = []
    if runtime_state == 'containers_absent':
        for port in required_ports:
            required_runtime.append({'port': port, 'matched': None, 'bindings': [], 'skipped': True})
    else:
        ingress_bindings = runtime_services.get('ingress') or []
        for port in required_ports:
            matched = [
                item
                for item in ingress_bindings
                if item.get('published') == port and item.get('target') == port and item.get('protocol') == 'tcp'
            ]
            required_runtime.append({'port': port, 'matched': bool(matched), 'bindings': matched})
            if not matched:
                runtime_issues.append(f'runtime ingress 缺少 {port}:{port}/tcp 发布')
            elif not any((item.get('host_ip') or '') == expected_ip for item in matched):
                runtime_issues.append(f'runtime ingress 的 {port}:{port}/tcp 未绑定到 {expected_ip}')
        extra_runtime = [
            item
            for item in ingress_bindings
            if item.get('published') not in required_ports or item.get('target') not in required_ports
        ]
        if extra_runtime:
            runtime_issues.append(f'runtime ingress 出现额外宿主机端口暴露：{extra_runtime}')
        if runtime_non_ingress_with_ports:
            runtime_issues.append('runtime 非 ingress 容器出现宿主机端口映射：' + ', '.join(sorted(runtime_non_ingress_with_ports)))

    runtime_contract_ok = not runtime_issues
    runtime_notes: list[str] = []
    if runtime_state == 'containers_absent':
        runtime_notes.append('未检测到任何 runtime 容器；当前仅校验 compose 暴露合同与 boundary 语义，跳过运行态端口绑定实证。')
    issues = list(compose.get('issues') or []) + runtime_issues + list(boundary.get('issues') or [])
    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'accepted': bool(compose.get('compose_contract_ok') and runtime_contract_ok and boundary.get('accepted') is True),
        'ingress_service': ingress_service,
        'expected_bind_ip': expected_ip,
        'compose_contract': compose,
        'runtime_contract': {
            'runtime_contract_ok': runtime_contract_ok,
            'runtime_state': runtime_state,
            'present_targets': sorted(runtime_present_targets),
            'required_host_ports': required_ports,
            'required_bindings': required_runtime,
            'service_port_bindings': runtime_services,
            'non_ingress_with_ports': sorted(runtime_non_ingress_with_ports),
            'notes': runtime_notes,
            'issues': runtime_issues,
        },
        'boundary_evidence': boundary,
        'issues': issues,
    }
