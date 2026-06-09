from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openclaw.doctor.platform import ingress_boundary_evidence_backend as backend
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))
POLICY_PATH = ROOT_DIR / 'config' / 'governance' / 'support' / 'ingress_boundary_evidence.json'
SCRIPT_PATH = ROOT_DIR / 'scripts' / 'doctor' / 'check_ingress_boundary_evidence.sh'
EXPORT_EVIDENCE_SCRIPT_PATH = ROOT_DIR / 'scripts' / 'runtime' / 'export_runtime_acceptance_evidence.sh'


class IngressBoundaryEvidenceBackendTest(unittest.TestCase):
    def _write_allowed_sources(self, tmp_path: Path, source_cidrs: list[str]) -> Path:
        allowed_sources_path = tmp_path / 'allowed_sources.json'
        allowed_sources_path.write_text(
            json.dumps(
                {
                    'schema_version': 1,
                    'accepted': True,
                    'source_cidrs': source_cidrs,
                    'issues': [],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return allowed_sources_path

    def test_normalize_source_cidrs_rejects_public_and_duplicate_ranges(self) -> None:
        payload, exit_code = backend.normalize_source_cidrs('10.0.0.0/24, 10.0.0.0/24, 8.8.8.0/24')

        self.assertEqual(exit_code, 2)
        self.assertFalse(payload['accepted'])
        self.assertIn('CIDR 重复：10.0.0.0/24', payload['issues'])
        self.assertIn('只允许私网或 loopback CIDR：8.8.8.0/24', payload['issues'])

    def test_compose_contract_summary_reads_truth_from_rendered_compose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            compose_path = Path(tmpdir) / 'docker-compose.yml'
            compose_path.write_text(
                '\n'.join(
                    (
                        'services:',
                        '  openclaw-private-ingress:',
                        '    image: nginx:latest',
                        '    ports:',
                        '      - "10.0.0.10:80:80"',
                        '      - "10.0.0.10:443:443"',
                        '  openclaw-runtime:',
                        '    image: busybox',
                    )
                ),
                encoding='utf-8',
            )

            summary = backend.compose_contract_summary(str(compose_path), '10.0.0.10', str(POLICY_PATH))

        self.assertTrue(summary['compose_contract_ok'])
        self.assertEqual(summary['ingress_service'], 'openclaw-private-ingress')
        self.assertFalse(summary['issues'])

    def test_compose_contract_summary_accepts_long_syntax_ipv6_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            compose_path = Path(tmpdir) / 'docker-compose.yml'
            compose_path.write_text(
                '\n'.join(
                    (
                        'services:',
                        '  openclaw-private-ingress:',
                        '    image: nginx:latest',
                        '    ports:',
                        '      - target: 80',
                        '        published: "80"',
                        '        protocol: tcp',
                        '        host_ip: "fd00::10"',
                        '      - target: 443',
                        '        published: "443"',
                        '        protocol: tcp',
                        '        host_ip: "fd00::10"',
                    )
                ),
                encoding='utf-8',
            )

            summary = backend.compose_contract_summary(str(compose_path), 'fd00::10', str(POLICY_PATH))

        self.assertTrue(summary['compose_contract_ok'])
        self.assertFalse(summary['issues'])

    def test_external_acl_boundary_contract_accepts_matching_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            allowed_sources_path = self._write_allowed_sources(tmp_path, ['10.0.0.0/24'])
            evidence_path = tmp_path / 'boundary.json'
            evidence_path.write_text(
                json.dumps(
                    {
                        'schema_version': 1,
                        'generated_at': '2026-04-22T00:00:00Z',
                        'source_cidrs': ['10.0.0.0/24'],
                        'allowed_ports': [80, 443],
                        'default_deny': True,
                        'ip_families': ['ipv4'],
                        'enforcement_plane': 'security_group',
                        'target_bind_ip': '10.0.0.10',
                        'target_hostnames': ['gateway.internal'],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            result = backend.evaluate_boundary_evidence(
                'external_acl',
                '10.0.0.10',
                'gateway.internal',
                str(allowed_sources_path),
                str(POLICY_PATH),
                str(evidence_path),
            )

        self.assertTrue(result['accepted'])
        self.assertEqual(result['method'], 'external_acl')
        self.assertFalse(result['issues'])

    def test_external_acl_boundary_contract_accepts_ipv6_family_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            allowed_sources_path = self._write_allowed_sources(tmp_path, ['fd00::/8'])
            evidence_path = tmp_path / 'boundary.json'
            evidence_path.write_text(
                json.dumps(
                    {
                        'schema_version': 1,
                        'generated_at': '2026-04-22T00:00:00Z',
                        'source_cidrs': ['fd00::/8'],
                        'allowed_ports': [80, 443],
                        'default_deny': True,
                        'ip_families': ['ipv6'],
                        'enforcement_plane': 'security_group',
                        'target_bind_ip': 'fd00::10',
                        'target_hostnames': ['gateway.internal'],
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            result = backend.evaluate_boundary_evidence(
                'external_acl',
                'fd00::10',
                'gateway.internal',
                str(allowed_sources_path),
                str(POLICY_PATH),
                str(evidence_path),
            )

        self.assertTrue(result['accepted'])
        self.assertEqual(result['required_ip_families'], ['ipv6'])

    def test_external_acl_boundary_contract_rejects_mismatched_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            allowed_sources_path = self._write_allowed_sources(tmp_path, ['10.0.0.0/24'])
            evidence_path = tmp_path / 'boundary.json'
            evidence_path.write_text(
                json.dumps(
                    {
                        'schema_version': 1,
                        'generated_at': '2026-04-22T00:00:00Z',
                        'source_cidrs': ['10.0.0.0/24'],
                        'allowed_ports': [80],
                        'default_deny': True,
                        'ip_families': ['ipv4'],
                        'enforcement_plane': 'security_group',
                        'target_bind_ip': '10.0.0.10',
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            result = backend.evaluate_boundary_evidence(
                'external_acl',
                '10.0.0.10',
                'gateway.internal',
                str(allowed_sources_path),
                str(POLICY_PATH),
                str(evidence_path),
            )

        self.assertFalse(result['accepted'])
        self.assertIn('allowed_ports 与部署输入不一致', '\n'.join(result['issues']))

    def test_host_firewall_boundary_contract_accepts_firewalld_zone_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            allowed_sources_path = self._write_allowed_sources(tmp_path, ['10.0.0.0/24'])
            zone_all = '\n'.join(
                (
                    'public (active)',
                    '  target: DROP',
                    '  interfaces: eth0',
                    '  sources: 10.0.0.0/24',
                    '  services: http https',
                    '  ports:',
                )
            )

            def command_side_effect(*args: str) -> tuple[int, str, str]:
                mapping = {
                    ('iptables-save',): (1, '', ''),
                    ('nft', 'list', 'ruleset'): (1, '', ''),
                    ('firewall-cmd', '--state'): (0, 'running', ''),
                    ('firewall-cmd', '--get-default-zone'): (0, 'public', ''),
                    ('firewall-cmd', '--get-active-zones'): (0, 'public\n  interfaces: eth0', ''),
                    ('firewall-cmd', '--get-zones'): (0, 'public', ''),
                    ('firewall-cmd', '--list-all-zones'): (0, zone_all, ''),
                    ('firewall-cmd', '--zone', 'public', '--list-all'): (0, zone_all, ''),
                    ('firewall-cmd', '--zone', 'public', '--list-rich-rules'): (0, '', ''),
                }
                return mapping.get(tuple(args), (1, '', ''))

            with mock.patch(
                'openclaw.doctor.platform.ingress_boundary_evidence_backend._command',
                side_effect=command_side_effect,
            ):
                result = backend.evaluate_boundary_evidence(
                    'host_firewall',
                    '10.0.0.10',
                    'gateway.internal',
                    str(allowed_sources_path),
                    str(POLICY_PATH),
                )

        self.assertTrue(result['accepted'])
        self.assertEqual(result['method'], 'firewalld')
        self.assertTrue(result['default_deny_ok'])
        self.assertFalse(result['issues'])

    def test_host_firewall_boundary_contract_accepts_iptables_docker_user_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            allowed_sources_path = self._write_allowed_sources(tmp_path, ['10.0.0.0/24'])
            iptables_snapshot = '\n'.join(
                (
                    '*filter',
                    ':INPUT ACCEPT [0:0]',
                    ':FORWARD ACCEPT [0:0]',
                    ':OUTPUT ACCEPT [0:0]',
                    ':DOCKER-USER - [0:0]',
                    '-A DOCKER-USER -p tcp -m conntrack --ctstate RELATED,ESTABLISHED --ctorigdst 10.0.0.10 --ctorigdstport 80 -j ACCEPT',
                    '-A DOCKER-USER -p tcp -m conntrack --ctstate RELATED,ESTABLISHED --ctorigdst 10.0.0.10 --ctorigdstport 443 -j ACCEPT',
                    '-A DOCKER-USER -s 10.0.0.0/24 -p tcp -m conntrack --ctorigdst 10.0.0.10 --ctorigdstport 80 -j ACCEPT',
                    '-A DOCKER-USER -s 10.0.0.0/24 -p tcp -m conntrack --ctorigdst 10.0.0.10 --ctorigdstport 443 -j ACCEPT',
                    '-A DOCKER-USER -p tcp -m conntrack --ctorigdst 10.0.0.10 --ctorigdstport 80 -j DROP',
                    '-A DOCKER-USER -p tcp -m conntrack --ctorigdst 10.0.0.10 --ctorigdstport 443 -j DROP',
                    'COMMIT',
                )
            )

            def command_side_effect(*args: str) -> tuple[int, str, str]:
                mapping = {
                    ('iptables-save',): (0, iptables_snapshot, ''),
                    ('nft', 'list', 'ruleset'): (1, '', ''),
                    ('firewall-cmd', '--state'): (1, '', ''),
                }
                return mapping.get(tuple(args), (1, '', ''))

            with mock.patch(
                'openclaw.doctor.platform.ingress_boundary_evidence_backend._command',
                side_effect=command_side_effect,
            ):
                result = backend.evaluate_boundary_evidence(
                    'host_firewall',
                    '10.0.0.10',
                    'gateway.internal',
                    str(allowed_sources_path),
                    str(POLICY_PATH),
                )

        self.assertTrue(result['accepted'])
        self.assertEqual(result['method'], 'iptables')
        self.assertTrue(result['default_deny_ok'])
        self.assertTrue(result['family_evidence'][0]['established_return_ok'])
        self.assertFalse(result['issues'])

    def test_shell_entry_uses_backend_module_without_inline_python(self) -> None:
        source = SCRIPT_PATH.read_text(encoding='utf-8')

        self.assertIn('-m openclaw.doctor.platform.ingress_boundary_evidence_backend', source)
        self.assertIn('-m openclaw.setup.network.gateway_ingress', source)
        self.assertIn('--require-nginx-policy', source)
        assert_static_text_absent(self, "<<'PY'", source)

    def test_runtime_evidence_export_requires_nginx_policy_closure(self) -> None:
        source = EXPORT_EVIDENCE_SCRIPT_PATH.read_text(encoding='utf-8')

        self.assertIn('.nginx_policy.required == true', source)
        self.assertIn('.nginx_policy.ok == true', source)
        self.assertIn('.nginx_policy.default_deny == true', source)
        self.assertIn('.nginx_policy.rewrite_phase_default_deny == true', source)
        self.assertIn('.nginx_policy.access_phase_default_deny == true', source)
        self.assertIn('sudo bash ./scripts/doctor/check_ingress_boundary_evidence.sh --env-file deploy/.env --require-nginx-policy', source)


if __name__ == '__main__':
    unittest.main()
