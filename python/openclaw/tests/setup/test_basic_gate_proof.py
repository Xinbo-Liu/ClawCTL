"""验证 basic gate proof 对 env、镜像、离线归档与 release 检查模式的绑定。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openclaw.setup.flow import basic_summary


class BasicGateProofTest(unittest.TestCase):
    """覆盖 one_click_test_basic proof 的写入、验证和失配阻断。"""

    CONTROL_PLANE_REF = 'registry.local/control-plane:v1@sha256:' + '2' * 64

    EXTRA_REF = 'registry.local/extra:v1@sha256:' + '9' * 64

    def _write_env(self, path: Path, *, gateway: str = 'registry.local/openclaw:v1@sha256:' + '1' * 64, include_extra: bool = False) -> None:
        """写出最小 deploy env，供 proof 哈希和镜像 ref 校验使用。"""
        lines = [
            f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE={gateway}',
            'OPENCLAW_RUNTIME_PYTHON_IMAGE=registry.local/runtime:v1@sha256:' + '3' * 64,
            'NGINX_IMAGE=registry.local/nginx:v1@sha256:' + '4' * 64,
        ]
        if include_extra:
            lines.append(f'OPENCLAW_EXTRA_IMAGE={self.EXTRA_REF}')
        path.write_text('\n'.join([*lines, '']), encoding='utf-8')

    def _write_pins(self, root: Path, *, control_plane: str | None = None, include_extra: bool = False) -> None:
        """写出测试仓库中的镜像 pin 合同真源。"""
        contracts_dir = root / 'config' / 'governance' / 'support'
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / 'repo_contracts.json').write_text(
            '{"schema_version":1,"contracts":['
            '{"id":"image_pins.openclaw","relative_path":"config/image_pins/openclaw.env","format":"env"},'
            '{"id":"image_pins.runtime","relative_path":"config/image_pins/runtime.env","format":"env"},'
            '{"id":"runtime.source_strategy","relative_path":"config/runtime/source_strategy.json","format":"json"}'
            ']}\n',
            encoding='utf-8',
        )
        pins_dir = root / 'config' / 'image_pins'
        pins_dir.mkdir(parents=True, exist_ok=True)
        (pins_dir / 'openclaw.env').write_text(
            'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=registry.local/openclaw:pin@sha256:' + '6' * 64 + '\n',
            encoding='utf-8',
        )
        runtime_lines = [
            f'OPENCLAW_CONTROL_PLANE_IMAGE={control_plane or self.CONTROL_PLANE_REF}',
            'OPENCLAW_RUNTIME_PYTHON_IMAGE=registry.local/runtime:pin@sha256:' + '7' * 64,
            'NGINX_IMAGE=registry.local/nginx:pin@sha256:' + '8' * 64,
        ]
        if include_extra:
            runtime_lines.append(f'OPENCLAW_EXTRA_IMAGE={self.EXTRA_REF}')
        (pins_dir / 'runtime.env').write_text('\n'.join([*runtime_lines, '']), encoding='utf-8')
        strategy_images = {
            'official_gateway': self._strategy_image('OPENCLAW_OFFICIAL_GATEWAY_IMAGE', 'config/image_pins/openclaw.env'),
            'control_plane_python': self._strategy_image('OPENCLAW_CONTROL_PLANE_IMAGE', 'config/image_pins/runtime.env', compose=False),
            'runtime_python': self._strategy_image('OPENCLAW_RUNTIME_PYTHON_IMAGE', 'config/image_pins/runtime.env'),
            'nginx_runtime': self._strategy_image('NGINX_IMAGE', 'config/image_pins/runtime.env'),
        }
        if include_extra:
            strategy_images['extra_runtime'] = self._strategy_image('OPENCLAW_EXTRA_IMAGE', 'config/image_pins/runtime.env')
        strategy_dir = root / 'config' / 'runtime'
        strategy_dir.mkdir(parents=True, exist_ok=True)
        strategy = {'schema_version': 1, 'images': strategy_images}
        (strategy_dir / 'source_strategy.json').write_text(json.dumps(strategy, ensure_ascii=False) + '\n', encoding='utf-8')

    def _strategy_image(self, env_key: str, pin_file: str, *, compose: bool = True) -> dict[str, object]:
        """生成最小 source_strategy image 条目。"""
        role = env_key.lower().replace('openclaw_', '').replace('_image', '')
        selector = role if role in {'gateway', 'ingress'} else 'default'
        return {
            'selected_runtime_source': {'ref_env': env_key, 'pin_file': pin_file},
            'deployment_contract': {'role': role, 'label': role, 'scope': 'runtime', 'enabled': True},
            'compose_runtime': {'enabled': compose, 'target_selector': selector} if compose else {'enabled': False},
        }

    def test_write_and_verify_gate_proof_matches_env_mode_and_images(self) -> None:
        """proof 必须绑定 env 哈希、模式和部署镜像输入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / 'deploy.env'
            proof_path = root / 'proof.json'
            results_path = root / 'results.txt'
            self._write_env(env_path)
            self._write_pins(root)
            results_path.write_text('PASS|env_file_exists||config\n', encoding='utf-8')
            options = basic_summary.parse_args(
                [
                    '--env-file',
                    str(env_path),
                    '--offline',
                    '1',
                    '--image-archive-path',
                    str(root / 'deployment_images.tar'),
                    '--return-code',
                    '0',
                    '--result-lines-file',
                    str(results_path),
                    '--proof-path',
                    str(proof_path),
                ]
            )

            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(basic_summary.write_gate_proof(options), 0)
                proof = basic_summary.build_gate_proof(options)
                self.assertEqual(proof['image_refs']['OPENCLAW_CONTROL_PLANE_IMAGE'], self.CONTROL_PLANE_REF)
                self.assertTrue(proof['mode']['release_check'])
                self.assertEqual(basic_summary.verify_gate_proof(options), 0)

            self._write_env(env_path, gateway='registry.local/openclaw:v2@sha256:' + '5' * 64)
            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                basic_summary.verify_gate_proof(options)

    def test_image_refs_follow_source_strategy_roles(self) -> None:
        """新增部署镜像角色时 proof 必须自动纳入，不依赖固定数量。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / 'deploy.env'
            self._write_env(env_path, include_extra=True)
            self._write_pins(root, include_extra=True)
            options = basic_summary.parse_args(
                [
                    '--env-file',
                    str(env_path),
                    '--offline',
                    '0',
                    '--return-code',
                    '0',
                ]
            )

            with patch.object(basic_summary, 'REPO_ROOT', root):
                proof = basic_summary.build_gate_proof(options)

            self.assertEqual(proof['image_refs']['OPENCLAW_EXTRA_IMAGE'], self.EXTRA_REF)

    def test_release_check_mode_is_part_of_gate_proof_key(self) -> None:
        """release 检查是否跳过必须进入 proof key，避免跳过和未跳过混用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / 'deploy.env'
            proof_path = root / 'proof.json'
            results_path = root / 'results.txt'
            self._write_env(env_path)
            self._write_pins(root)
            results_path.write_text('PASS|env_file_exists||config\n', encoding='utf-8')
            base_args = [
                '--env-file',
                str(env_path),
                '--offline',
                '0',
                '--return-code',
                '0',
                '--result-lines-file',
                str(results_path),
                '--proof-path',
                str(proof_path),
            ]
            write_options = basic_summary.parse_args([*base_args, '--release-check', '0'])
            verify_options = basic_summary.parse_args([*base_args, '--release-check', '1'])

            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(basic_summary.write_gate_proof(write_options), 0)
                proof = basic_summary.build_gate_proof(write_options)
                self.assertFalse(proof['mode']['release_check'])
                self.assertEqual(proof['mode']['release_policy'], 'skipped')

            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                basic_summary.verify_gate_proof(verify_options)

    def test_release_policy_is_part_of_gate_proof_key(self) -> None:
        """relaxed_install 与 strict_release 的 proof 不能混用。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / 'deploy.env'
            proof_path = root / 'proof.json'
            results_path = root / 'results.txt'
            self._write_env(env_path)
            self._write_pins(root)
            results_path.write_text('WARN|openclaw_release_alignment|latest newer|release\n', encoding='utf-8')
            base_args = [
                '--env-file',
                str(env_path),
                '--offline',
                '0',
                '--return-code',
                '0',
                '--result-lines-file',
                str(results_path),
                '--proof-path',
                str(proof_path),
                '--release-check',
                '1',
            ]
            relaxed_options = basic_summary.parse_args([*base_args, '--release-policy', 'relaxed_install'])
            strict_options = basic_summary.parse_args([*base_args, '--release-policy', 'strict_release'])

            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(basic_summary.write_gate_proof(relaxed_options), 0)
                proof = basic_summary.build_gate_proof(relaxed_options)
                self.assertEqual(proof['mode']['release_policy'], 'relaxed_install')

            with patch.object(basic_summary, 'REPO_ROOT', root), redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                basic_summary.verify_gate_proof(strict_options)

    def test_offline_proof_records_auto_discovered_archive(self) -> None:
        """离线模式 proof 必须记录自动发现的镜像归档路径。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / 'deploy.env'
            artifact_dir = root / 'state' / 'image_artifacts'
            artifact_dir.mkdir(parents=True)
            old_archive = artifact_dir / 'deployment_images_20260101.tar'
            latest_archive = artifact_dir / 'deployment_images_20260201.tar'
            old_archive.write_text('old', encoding='utf-8')
            latest_archive.write_text('latest', encoding='utf-8')
            os.utime(old_archive, (1, 1))
            os.utime(latest_archive, (2, 2))
            self._write_env(env_path)
            self._write_pins(root)
            options = basic_summary.parse_args(
                [
                    '--env-file',
                    str(env_path),
                    '--offline',
                    '1',
                    '--return-code',
                    '0',
                ]
            )

            with patch.object(basic_summary, 'REPO_ROOT', root):
                proof = basic_summary.build_gate_proof(options)
            self.assertEqual(proof['image_archive_path'], str(latest_archive.resolve()))


if __name__ == '__main__':
    unittest.main()
