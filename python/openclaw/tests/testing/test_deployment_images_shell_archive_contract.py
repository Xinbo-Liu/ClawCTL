"""验证部署镜像 shell 归档合同解析在 bundle 与 legacy 路径上失败闭合。"""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.source_strategy import deployment_image_roles
from openclaw.setup.deploy_env.query import parse_env_file


ROOT_DIR = resolve_repo_root(Path(__file__))


class DeploymentImagesShellArchiveContractTest(unittest.TestCase):
    """覆盖 shell 归档解析器对合同、RepoTags 和 digest 漂移的判定。"""

    def setUp(self) -> None:
        """准备 bash/jq/tar 前提和当前仓库镜像 pin refs。"""
        if os.name == 'nt':
            self.skipTest('Windows Git Bash tar/jq path handling is not stable for this shell archive parser test')
        self.bash = resolve_bash_executable()
        if not self.bash:
            self.skipTest('未找到可用 bash；跳过 shell 归档合同测试')
        jq_check = subprocess.run(
            [str(self.bash), '-lc', 'command -v jq >/dev/null 2>&1 && command -v tar >/dev/null 2>&1'],
            cwd=ROOT_DIR,
            check=False,
        )
        if jq_check.returncode != 0:
            self.skipTest('缺少 jq/tar；跳过 shell 归档合同测试')
        pins = parse_env_file(ROOT_DIR / 'config' / 'image_pins' / 'runtime.env')
        pins.update(parse_env_file(ROOT_DIR / 'config' / 'image_pins' / 'openclaw.env'))
        self.refs = [pins[role.env_key] for role in deployment_image_roles(ROOT_DIR)]

    def _bash_path(self, path: Path) -> str:
        """将临时路径转换为当前 bash 运行时可识别的路径。"""
        if os.name != 'nt':
            return path.as_posix()
        result = subprocess.run(
            [str(self.bash), '-lc', 'cygpath -u "$1"', 'openclaw-path', str(path)],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest('无法把 Windows 临时目录转换为 Git Bash 路径')
        return result.stdout.strip()

    def _write_tar(self, path: Path, entries: dict[str, bytes]) -> None:
        """写出只包含指定条目的最小 tar，避免测试依赖 Docker daemon。"""
        with tarfile.open(path, 'w') as archive:
            for name, content in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, BytesIO(content))

    def _legacy_manifest(self, tags: list[str] | None) -> bytes:
        """生成 legacy docker save manifest.json 测试载荷。"""
        return json.dumps([{'Config': 'config.json', 'RepoTags': tags, 'Layers': []}]).encode()

    def _bundle_contract(self, refs: list[str]) -> bytes:
        """生成 deployment image bundle 合同测试载荷。"""
        roles = [{'role': f'role_{index}', 'env_key': f'KEY_{index}', 'pin_ref': ref} for index, ref in enumerate(refs)]
        return json.dumps({'schemaVersion': 1, 'kind': 'openclaw_deployment_image_bundle', 'roles': roles}).encode()

    def _verify_archive(self, archive_path: Path) -> subprocess.CompletedProcess[str]:
        """调用 shell 合同解析函数并返回原始进程结果。"""
        archive_arg = self._bash_path(archive_path)
        script = 'source scripts/lib/deployment_images.sh; deployment_images_archive_verify_required_refs "$1" "${@:2}"'
        try:
            return subprocess.run(
                [str(self.bash), '-lc', script, 'verify-archive', archive_arg, *self.refs],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                env={**os.environ, 'OPENCLAW_REPO_CONTRACTS_FORCE_AWK': '1'},
                check=False,
                timeout=20,
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(f'deployment image archive parser timed out: {exc}')

    def test_bundle_contract_passes(self) -> None:
        """bundle 合同完整覆盖当前 pin refs 时校验通过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / 'bundle.tar'
            self._write_tar(archive, {'deployment-images.contract.json': self._bundle_contract(self.refs)})

            result = self._verify_archive(archive)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_legacy_raw_tar_passes_with_repo_tags(self) -> None:
        """legacy raw tar 仍可通过 RepoTags 覆盖当前 pin tag 的兼容路径。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / 'legacy.tar'
            self._write_tar(archive, {'manifest.json': self._legacy_manifest(self.refs)})

            result = self._verify_archive(archive)

            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_legacy_repo_tags_null_fails_closed(self) -> None:
        """legacy manifest 的 RepoTags:null 必须失败闭合。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / 'legacy-null.tar'
            self._write_tar(archive, {'manifest.json': self._legacy_manifest(None)})

            result = self._verify_archive(archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(self.refs[0], result.stdout)

    def test_bundle_missing_role_fails_closed(self) -> None:
        """bundle 缺少任一当前 pin ref 时必须失败闭合。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / 'bundle-missing.tar'
            self._write_tar(archive, {'deployment-images.contract.json': self._bundle_contract(self.refs[:-1])})

            result = self._verify_archive(archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(self.refs[-1], result.stdout)

    def test_bundle_digest_mismatch_fails_closed(self) -> None:
        """bundle 中 digest 不一致的 pin ref 不得被 source tag 混淆放行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / 'bundle-digest-mismatch.tar'
            wrong_refs = [*self.refs]
            wrong_refs[0] = wrong_refs[0].split('@sha256:', 1)[0] + '@sha256:' + '0' * 64
            self._write_tar(archive, {'deployment-images.contract.json': self._bundle_contract(wrong_refs)})

            result = self._verify_archive(archive)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(self.refs[0], result.stdout)


if __name__ == '__main__':
    unittest.main()
