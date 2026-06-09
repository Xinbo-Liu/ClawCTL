from __future__ import annotations

import json
from pathlib import Path
import unittest

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.tests.support.static_text_assertions import assert_static_text_absent


ROOT_DIR = resolve_repo_root(Path(__file__))
TRUTH_LIB = ROOT_DIR / 'scripts' / 'lib' / 'docker_host_support_truth.sh'
SYSTEM_TIME_LIB = ROOT_DIR / 'scripts' / 'lib' / 'system_time_guard.sh'
PREPARE_SCRIPT = ROOT_DIR / 'scripts' / 'setup' / 'prepare_docker_host.sh'
READINESS_SCRIPT = ROOT_DIR / 'scripts' / 'doctor' / 'check_docker_host_readiness.sh'
CHECK_SYSTEM_TIME_SCRIPT = ROOT_DIR / 'scripts' / 'doctor' / 'check_system_time.sh'
UPDATE_SYSTEM_TIME_SCRIPT = ROOT_DIR / 'scripts' / 'setup' / 'update_system_time.sh'


class DockerHostShellTruthTest(unittest.TestCase):
    def test_prepare_docker_host_uses_shared_truth_reader(self) -> None:
        source = PREPARE_SCRIPT.read_text(encoding='utf-8')

        self.assertTrue(TRUTH_LIB.is_file())
        self.assertTrue(SYSTEM_TIME_LIB.is_file())
        self.assertIn('scripts/lib/docker_host_support_truth.sh', source)
        self.assertIn('scripts/lib/system_time_guard.sh', source)
        self.assertIn('system_time_guard_update', source)
        self.assertIn('preflight_system_time_before_network_install', source)
        self.assertIn('docker_host_support_supported_centos7_section_value', source)
        self.assertIn('docker_host_support_supported_centos7_vault_repo_candidates', source)
        self.assertIn('docker_host_support_supported_centos7_docker_repo_candidates', source)
        self.assertIn('docker_host_support_supported_centos7_registry_mirrors', source)
        self.assertIn('docker_host_support_supported_centos7_network_profile_value', source)
        self.assertIn('--centos7-vault-source', source)
        self.assertIn('OPENCLAW_CENTOS7_VAULT_BASE_URL', source)
        self.assertIn('--docker-repo-source', source)
        self.assertIn('--network-profile', source)
        self.assertIn('OPENCLAW_DEPLOY_NETWORK_PROFILE', source)
        self.assertIn('OPENCLAW_DOCKER_REGISTRY_MIRRORS', source)
        self.assertIn('yum makecache 预热失败', source)
        assert_static_text_absent(self, 'read_policy_value() {', source)

    def test_docker_host_truth_declares_network_profiles(self) -> None:
        truth_source = TRUTH_LIB.read_text(encoding='utf-8')
        support = json.loads((ROOT_DIR / 'config' / 'governance' / 'support' / 'docker_host.json').read_text(encoding='utf-8'))
        profiles = {
            str(item.get('id')): item
            for item in support['policies']['supported_centos7']['network_profiles']
        }
        examples = support['entrypoint']['command_examples']

        self.assertEqual(profiles['cn']['centos7_vault_source'], 'aliyun_cn')
        self.assertEqual(profiles['cn']['docker_repo_source'], 'aliyun_cn')
        self.assertEqual(profiles['global']['centos7_vault_source'], 'official')
        self.assertEqual(profiles['global']['docker_repo_source'], 'official')
        self.assertIn('sudo bash ./scripts/setup/prepare_docker_host.sh --all --network-profile cn', examples)
        self.assertIn('sudo bash ./scripts/setup/prepare_docker_host.sh --repair-centos7-vault-repos --network-profile cn', examples)
        self.assertIn('sudo bash ./scripts/setup/prepare_docker_host.sh --install-docker --install-compose --network-profile cn --configure-daemon', examples)
        self.assertIn('docker_host_support_supported_centos7_network_profile_value', truth_source)
        self.assertIn('cn:centos7_vault_source', truth_source)
        self.assertIn('global:docker_repo_source', truth_source)

    def test_check_docker_host_readiness_uses_shared_truth_reader(self) -> None:
        source = READINESS_SCRIPT.read_text(encoding='utf-8')

        self.assertIn('scripts/lib/docker_host_support_truth.sh', source)
        self.assertIn('scripts/lib/repo_contracts.sh', source)
        self.assertIn('scripts/doctor/check_system_time.sh', source)
        self.assertIn('repo_contract_path runtime.source_strategy', source)
        self.assertIn('docker_host_support_supported_centos7_section_value', source)
        self.assertIn('docker_host_support_supported_centos7_scalar', source)
        self.assertIn('host_command_path()', source)
        self.assertIn('"/usr/sbin/$cmd" "/sbin/$cmd" "/usr/bin/$cmd" "/bin/$cmd"', source)
        self.assertIn('host_command_exists "$cmd" || fail "CentOS 7 宿主机支持策略要求存在命令：$cmd"', source)
        assert_static_text_absent(self, '.policies.supported_centos7.docker_server.minimum', source)

    def test_firewalld_docker_zone_contract_is_prepared_and_checked(self) -> None:
        prepare_source = PREPARE_SCRIPT.read_text(encoding='utf-8')
        readiness_source = READINESS_SCRIPT.read_text(encoding='utf-8')

        self.assertIn('ensure_docker_firewalld_zone()', prepare_source)
        self.assertIn('firewall-cmd --permanent --new-zone=docker', prepare_source)
        self.assertIn('firewall-cmd --permanent --zone=docker --set-target=ACCEPT', prepare_source)
        self.assertIn('smoke_test_docker_bridge_network', prepare_source)
        self.assertIn('INVALID_ZONE.*docker', prepare_source)
        self.assertIn('check_firewalld_docker_zone_contract', readiness_source)
        self.assertIn('permanent docker zone 缺失', readiness_source)
        self.assertIn('target=${target:-<empty>}，应为 ACCEPT', readiness_source)

    def test_system_time_entries_share_single_guard_library(self) -> None:
        check_source = CHECK_SYSTEM_TIME_SCRIPT.read_text(encoding='utf-8')
        update_source = UPDATE_SYSTEM_TIME_SCRIPT.read_text(encoding='utf-8')
        lib_source = SYSTEM_TIME_LIB.read_text(encoding='utf-8')

        self.assertIn('scripts/lib/system_time_guard.sh', check_source)
        self.assertIn('system_time_guard_check "${args[@]}"', check_source)
        self.assertIn('scripts/lib/system_time_guard.sh', update_source)
        self.assertIn('system_time_guard_update "${args[@]}"', update_source)
        self.assertIn('system_time_guard_reference_epoch()', lib_source)
        self.assertIn('curl -k -sS -I -L', lib_source)
        self.assertIn('date -u -s "@$reference_epoch"', lib_source)

if __name__ == '__main__':
    unittest.main()
