"""验证受管文档清单、动态扩展归属与无 Git 交付包边界。"""
from __future__ import annotations

import base64
import json
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from openclaw.control_plane.extensions.fragment_descriptors import DOCS_REGISTRY_DESCRIPTOR, load_fragment_payload
from openclaw.doctor.agent_modules.managed_probe_fixture import materialize_managed_probe_extension
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import ensure_repo_markers, read_json, write_json
from openclaw.docs.support import docs_registry
from openclaw.docs.validators.inventory import inventory_errors, load_inventory
from openclaw.docs.validators.registry_context import load_validator_context
from openclaw.lib.repo.contracts import repo_contract_path
from openclaw.lib.repo.layout import DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH


def _page(path: str, **extra: object) -> dict[str, object]:
    """创建完整身份的小型文档注册项，供覆盖与 owner 场景复用。"""
    return {'path': path, 'role': 'local', 'entryLevel': 'local', 'ownerDomain': 'fixture', 'sourceMode': 'manual', 'formalEntry': False, 'localOnly': True, **extra}


def _seed_repository(root: Path, page_path: str) -> Path:
    """建立由真实 control-plane loader 可读取的最小文档仓库。

    参数：
        root（Path）：临时仓库根目录。
        page_path（str）：基座注册页的仓内路径。
    返回：
        Path：该仓库的文档注册表真源路径。
    副作用：
        复制既有 fixture 所需合同、service 和 schemas，并在临时根内写入注册表与空扩展索引。
    """
    ensure_repo_markers(root, docs_registry.ROOT_DIR)
    write_json(root / 'agent/extensions/index.json', {'extensions': []})
    registry = repo_contract_path('governance.docs_registry', root_dir=root)
    write_json(registry, {'pages': [_page(page_path)]})
    return registry


def _attach_probe_docs(root: Path) -> tuple[Path, Path, str]:
    """为真实受管扩展 fixture 添加本地文档 fragment。

    参数：
        root（Path）：已有基座文档注册表的临时仓库根。
    返回：
        tuple[Path, Path, str]：扩展 profile、fragment 真源及其注册页路径。
    副作用：
        仅在临时仓库内生成扩展 fixture、文档 fragment，并更新该 manifest 的治理声明。
    """
    fixture = materialize_managed_probe_extension(root, base_repo_root=docs_registry.ROOT_DIR, use_snapshot_cache=False)
    page_path = fixture.package_root.relative_to(root).as_posix() + '/README.md'
    fragment = fixture.manifest_dir / 'probe.docs_registry.json'
    write_json(fragment, {'pages': [_page(page_path)]})
    manifest = read_json(fixture.manifest_path)
    manifest.setdefault('governanceSurfaces', {})['docsRegistryPath'] = fragment.name
    write_json(fixture.manifest_path, manifest)
    return fixture.service_path, fragment, page_path


class DocumentationInventoryTest(unittest.TestCase):
    """覆盖 Git NUL 清单、BOM、路径与归属失败。"""

    def test_host_inventory_keeps_spaces_chinese_and_ignores_non_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '中文 页面.md').write_text('# 页面\n', encoding='utf-8')
            (root / 'docs').mkdir()
            (root / 'docs/page.MD').write_text('# 页面\n', encoding='utf-8')
            encoded = base64.b64encode('中文 页面.md\0docs/page.MD\0deploy/.env\0'.encode()).decode()
            paths = load_inventory(root, environ={'OPENCLAW_DOCS_TRACKED_FILES_B64': encoded, 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)})
            self.assertEqual(paths, {'中文 页面.md', 'docs/page.MD'})

    @unittest.skipIf(os.name == 'nt', 'Windows 文件名不允许换行；Linux NUL 清单仍需保留该路径')
    def test_host_nul_snapshot_keeps_newline_in_document_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = '换行\n页面.md'
            (root / name).write_text('# 页面\n', encoding='utf-8')
            self.assertEqual(load_inventory(root, environ={
                'OPENCLAW_DOCS_TRACKED_FILES_B64': base64.b64encode((name + '\0').encode()).decode(),
                'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root),
            }), {name})

    def test_bundle_uses_existing_bom_and_missing_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# 页面\n', encoding='utf-8')
            bom = root / 'export.bom.json'
            bom.write_text(json.dumps({'files': [{'path': 'README.md'}, {'path': 'python/tool.py'}]}), encoding='utf-8')
            self.assertEqual(load_inventory(root, environ={'OPENCLAW_DOCS_INVENTORY_BOM_PATH': str(bom)}), {'README.md'})
            with self.assertRaisesRegex(ValueError, '没有 .git'):
                load_inventory(root, environ={})

    def test_explicit_bom_precedes_invalid_or_foreign_host_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# 页面\n', encoding='utf-8')
            bom = root / 'export.bom.json'
            bom.write_text(json.dumps({'files': [{'path': 'README.md'}]}), encoding='utf-8-sig')
            for encoded, binding in [('!invalid', str(root / 'foreign')), ('', '')]:
                with self.subTest(encoded=encoded):
                    inventory = load_inventory(root, environ={
                        'OPENCLAW_DOCS_INVENTORY_BOM_PATH': str(bom),
                        'OPENCLAW_DOCS_TRACKED_FILES_B64': encoded,
                        'OPENCLAW_DOCS_TRACKED_FILES_ROOT': binding,
                    })
                    self.assertEqual(inventory, {'README.md'})

    def test_bom_declared_missing_document_fails_instead_of_silently_reducing_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bom = root / 'export.bom.json'
            bom.write_text(json.dumps({'files': [{'path': 'missing.md'}]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'BOM.*Markdown 文件不存在：missing.md'):
                load_inventory(root, environ={'OPENCLAW_DOCS_INVENTORY_BOM_PATH': str(bom)})

    def test_host_snapshot_requires_matching_root_and_accepts_bound_empty_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for binding in (None, '', str(root / 'foreign')):
                env = {'OPENCLAW_DOCS_TRACKED_FILES_B64': ''}
                if binding is not None:
                    env['OPENCLAW_DOCS_TRACKED_FILES_ROOT'] = binding
                with self.subTest(binding=binding), self.assertRaises(ValueError):
                    load_inventory(root, environ=env)
            self.assertEqual(load_inventory(root, environ={
                'OPENCLAW_DOCS_TRACKED_FILES_B64': '',
                'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root / 'unused' / '..'),
            }), set())

    def test_direct_git_uses_case_insensitive_nul_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            (root / 'README.MD').write_text('# 页面\n', encoding='utf-8')
            snapshot = b'README.MD\0'
            with patch('openclaw.docs.validators.inventory.subprocess.run', return_value=SimpleNamespace(stdout=snapshot)) as git:
                paths = load_inventory(root, environ={})
            self.assertEqual(git.call_args.args[0][-2:], ['--', ':(icase)*.md'])
            self.assertEqual(paths, load_inventory(root, environ={
                'OPENCLAW_DOCS_TRACKED_FILES_B64': base64.b64encode(snapshot).decode(),
                'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root),
            }))

    def test_inventory_cannot_escape_root_or_accept_invalid_base64(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in ('../private.md', '/absolute.md', 'docs\\page.md'):
                encoded = base64.b64encode((path + '\0').encode()).decode()
                with self.subTest(path=path), self.assertRaises(ValueError):
                    load_inventory(root, environ={'OPENCLAW_DOCS_TRACKED_FILES_B64': encoded, 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)})
            with self.assertRaises(ValueError):
                load_inventory(root, environ={'OPENCLAW_DOCS_TRACKED_FILES_B64': '!invalid', 'OPENCLAW_DOCS_TRACKED_FILES_ROOT': str(root)})

    def test_unstaged_tracked_deletion_passes_only_after_registration_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            with patch('openclaw.docs.validators.inventory.subprocess.run', return_value=SimpleNamespace(stdout=b'removed.md\0')):
                inventory = load_inventory(root, environ={})
            self.assertEqual(inventory, set())
            with patch('openclaw.docs.validators.inventory.managed_explicit_extensions', return_value=()):
                self.assertEqual(inventory_errors({'pages': []}, inventory, root_dir=root), [])
                errors = inventory_errors({'pages': [_page('removed.md')]}, inventory, root_dir=root)
            self.assertTrue(any('登记页不在受管 Markdown' in error for error in errors))
            self.assertTrue(any('登记页不是现存文件' in error for error in errors))

    def test_coverage_reports_missing_registration_and_owned_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension_root = root / 'agent/extensions/agent_example'
            extension_root.mkdir(parents=True)
            path = 'agent/extensions/agent_example/README.md'
            (root / path).write_text('# 局部说明\n', encoding='utf-8')
            extension = SimpleNamespace(id='agent_example', root_dir=extension_root)
            with patch('openclaw.docs.validators.inventory.managed_explicit_extensions', return_value=(extension,)):
                errors = inventory_errors({'pages': [_page(path)]}, {path, 'missing.md'}, root_dir=root)
                self.assertEqual(len(errors), 2)
                self.assertIn('受管 Markdown 未登记：missing.md', errors)
                self.assertIn('extensionId 应为 agent_example', errors[1])
                self.assertEqual(inventory_errors({'pages': [_page(path, extensionId='agent_example')]}, {path}, root_dir=root), [])

    def test_identity_does_not_allow_local_formal_entry(self) -> None:
        with self.assertRaisesRegex(SystemExit, '局部文档不能'):
            docs_registry.require_pages({'pages': [_page('README.md', formalEntry=True)]}, validate_metadata=True)


class RepositoryRegistryTest(unittest.TestCase):
    """用两个独立根目录验证动态扩展合并和 batch 范围恢复。"""

    def test_real_descriptor_rejects_non_object_pages_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'docs_registry.json'
            fragment = root / 'extension.docs_registry.json'
            for payload in ({}, {'pages': {}}, {'pages': [_page('valid.md'), None]}, {'pages': [7]}):
                write_json(registry, payload)
                with self.subTest(base=payload), self.assertRaisesRegex(ValueError, 'docs_registry.pages'):
                    load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=[])
            write_json(registry, {'pages': []})
            for pages in ({}, [None], [_page('valid.md'), 7]):
                write_json(fragment, {'pages': pages})
                with self.subTest(fragment=pages), self.assertRaisesRegex(ValueError, 'docs_registry.pages'):
                    load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=[{
                        'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment},
                    }])
            write_json(fragment, {'checker': {'example': 'fixture'}})
            loaded = load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=[{
                'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment},
            }])
            self.assertEqual(loaded['pages'], [])
            self.assertEqual(loaded['checker'], {'example': 'fixture'})

    def test_real_active_loader_uses_requested_root_and_rejects_foreign_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('first', 'second')]
            for root in roots:
                _seed_repository(root, root.name + '.md')
            first_service = roots[0] / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
            second_service = roots[1] / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
            with docs_registry.repository_registry_scope():
                first = docs_registry.load_check_registry(first_service, root_dir=roots[0], explicit_config=True)
                second = docs_registry.load_check_registry(second_service, root_dir=roots[1], explicit_config=True)
                self.assertEqual([page['path'] for page in first['pages']], ['first.md'])
                self.assertEqual([page['path'] for page in second['pages']], ['second.md'])
                with self.assertRaisesRegex(ValueError, 'service.*根目录'):
                    docs_registry.load_check_registry(first_service, root_dir=roots[1], explicit_config=True)

    def test_real_descriptor_rejects_base_forging_correct_extension_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'docs_registry.json'
            fragment = root / 'extension.docs_registry.json'
            page_path = 'agent/extensions/agent_example/README.md'
            write_json(registry, {'pages': [_page(page_path, extensionId='agent_example')]})
            write_json(fragment, {'pages': []})
            with self.assertRaisesRegex(ValueError, '基座页不能声明 extensionId.*fragment'):
                load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=[{
                    'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment},
                }])

    def test_real_descriptor_keeps_base_unowned_and_injects_fragment_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'docs_registry.json'
            fragment = root / 'extension.docs_registry.json'
            page_path = 'agent/extensions/agent_example/README.md'
            write_json(registry, {'pages': [_page('README.md')]})
            extensions = [{'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment}}]
            for fragment_page in (_page(page_path), _page(page_path, extensionId='agent_example')):
                write_json(fragment, {'pages': [fragment_page]})
                loaded = load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=extensions)
                self.assertEqual(loaded['pages'], [_page('README.md'), _page(page_path, extensionId='agent_example')])
            write_json(fragment, {'pages': [_page(page_path, extensionId='agent_other')]})
            with self.assertRaisesRegex(ValueError, 'extensionId conflict'):
                load_fragment_payload(DOCS_REGISTRY_DESCRIPTOR, path=registry, extensions=extensions)

    def test_real_repository_scope_refreshes_fragments_and_contract_path_between_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = _seed_repository(root, 'first.md')
            _, fragment, probe_page = _attach_probe_docs(root)
            service = root / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
            with docs_registry.repository_registry_scope():
                initial = docs_registry.load_check_registry(service, root_dir=root)
                with docs_registry.repository_registry_scope():
                    self.assertIs(initial, docs_registry.load_check_registry(service, root_dir=root))
                self.assertEqual({page['path'] for page in initial['pages']}, {'first.md', probe_page})
            changed_probe = probe_page.replace('README.md', 'operations.md')
            write_json(fragment, {'pages': [_page(changed_probe)]})
            alternative = original.with_name('alternative_docs_registry.json')
            write_json(alternative, {'pages': [_page('second.md')]})
            truth_path = root / 'config/governance/support/repo_contracts.json'
            truth = read_json(truth_path)
            for contract in truth['contracts']:
                if contract['id'] == 'governance.docs_registry':
                    contract['relative_path'] = alternative.relative_to(root).as_posix()
            write_json(truth_path, truth)
            with docs_registry.repository_registry_scope():
                refreshed = docs_registry.load_check_registry(service, root_dir=root)
            self.assertEqual({page['path'] for page in refreshed['pages']}, {'second.md', changed_probe})

    def test_real_disabled_extension_is_repository_only_until_profile_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_repository(root, 'base.md')
            probe_service, _, probe_page = _attach_probe_docs(root)
            platform_service = root / DEFAULT_RUNTIME_CONTROL_PLANE_SERVICE_CONFIG_REL_PATH
            with docs_registry.repository_registry_scope():
                repository = docs_registry.load_check_registry(platform_service, root_dir=root)
                active = docs_registry.load_check_registry(platform_service, root_dir=root, explicit_config=True)
                selected = docs_registry.load_check_registry(probe_service, root_dir=root, explicit_config=True)
            self.assertIn(probe_page, {page['path'] for page in repository['pages']})
            self.assertNotIn(probe_page, {page['path'] for page in active['pages']})
            self.assertIn(_page(probe_page, extensionId='agent_probe'), selected['pages'])

    def test_context_converts_registry_system_exit_into_failure(self) -> None:
        stderr = io.StringIO()
        with patch('openclaw.docs.validators.registry_context.load_check_registry', side_effect=SystemExit('bad registry')):
            with redirect_stderr(stderr):
                result = load_validator_context([], usage_text='fixture usage', error_prefix='[fixture]')
        self.assertEqual(result, 1)
        self.assertIn('[fixture] bad registry', stderr.getvalue())

    def test_repository_loader_rejects_base_and_fragment_page_conflict(self) -> None:
        docs_registry.load_repository_registry.cache_clear()
        self.addCleanup(docs_registry.load_repository_registry.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / 'docs_registry.json'
            fragment_path = root / 'extension.docs_registry.json'
            registry_path.write_text(json.dumps({'pages': [_page('shared.md')]}), encoding='utf-8')
            fragment_path.write_text(json.dumps({'pages': [_page('shared.md', role='reference')]}), encoding='utf-8')
            extension = {'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment_path}}
            with (
                patch('openclaw.lib.repo.managed_extensions.managed_explicit_extensions', return_value=()),
                patch('openclaw.control_plane.registry_loader.config.load_registry_service_context', return_value={'extensions': [extension]}),
                patch('openclaw.lib.repo.contracts.repo_contract_path', return_value=registry_path),
            ):
                with self.assertRaisesRegex(ValueError, r'docs_registry\.pages\.shared\.md\.role conflict'):
                    docs_registry.load_repository_registry(root)

    def test_repository_loader_rejects_two_extension_owners_for_same_page(self) -> None:
        docs_registry.load_repository_registry.cache_clear()
        self.addCleanup(docs_registry.load_repository_registry.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / 'docs_registry.json'
            registry_path.write_text(json.dumps({'pages': []}), encoding='utf-8')
            extensions = []
            for extension_id in ('agent_first', 'agent_second'):
                fragment_path = root / f'{extension_id}.docs_registry.json'
                fragment_path.write_text(json.dumps({'pages': [_page('shared.md')]}), encoding='utf-8')
                extensions.append({'id': extension_id, 'governanceSurfaces': {'docsRegistryPath': fragment_path}})
            with (
                patch('openclaw.lib.repo.managed_extensions.managed_explicit_extensions', return_value=()),
                patch('openclaw.control_plane.registry_loader.config.load_registry_service_context', return_value={'extensions': extensions}),
                patch('openclaw.lib.repo.contracts.repo_contract_path', return_value=registry_path),
            ):
                with self.assertRaisesRegex(ValueError, r'docs_registry\.pages\.shared\.md\.extensionId conflict'):
                    docs_registry.load_repository_registry(root)

    def test_repository_loader_rejects_same_extension_with_different_profile_fragments(self) -> None:
        docs_registry.load_repository_registry.cache_clear()
        self.addCleanup(docs_registry.load_repository_registry.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / 'docs_registry.json'
            registry_path.write_text(json.dumps({'pages': []}), encoding='utf-8')
            first_fragment = root / 'first.docs_registry.json'
            second_fragment = root / 'second.docs_registry.json'
            first_fragment.write_text(json.dumps({'pages': [_page('first.md')]}), encoding='utf-8')
            second_fragment.write_text(json.dumps({'pages': [_page('second.md')]}), encoding='utf-8')
            extension_config = root / 'agent/extensions/agent_example/service.json'
            managed_extension = SimpleNamespace(default_service_config_path=extension_config)
            selected_fragment = first_fragment

            def context(config: Path) -> dict[str, object]:
                fragment_path = selected_fragment if config == extension_config else first_fragment
                return {'extensions': [{'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': fragment_path}}]}

            with (
                patch('openclaw.lib.repo.managed_extensions.managed_explicit_extensions', return_value=(managed_extension,)),
                patch('openclaw.control_plane.registry_loader.config.load_registry_service_context', side_effect=context),
                patch('openclaw.lib.repo.contracts.repo_contract_path', return_value=registry_path),
            ):
                loaded = docs_registry.load_repository_registry(root)
                self.assertEqual(loaded['pages'], [_page('first.md', extensionId='agent_example')])
                docs_registry.load_repository_registry.cache_clear()
                selected_fragment = second_fragment
                with self.assertRaisesRegex(ValueError, '扩展 agent_example.*不同 profile 中不一致'):
                    docs_registry.load_repository_registry(root)

    def test_repository_loader_discovers_fragments_once_and_isolates_roots(self) -> None:
        docs_registry.load_repository_registry.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            roots = [base / 'first', base / 'second']
            for root in roots:
                root.mkdir()
            def context(config: Path) -> dict[str, object]:
                return {'extensions': [{'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': config.parent / 'docs.json'}}]}
            with (
                patch('openclaw.lib.repo.managed_extensions.managed_explicit_extensions', return_value=()),
                patch('openclaw.control_plane.registry_loader.config.load_registry_service_context', side_effect=context),
                patch('openclaw.lib.repo.contracts.repo_contract_path', side_effect=lambda _, root_dir: root_dir / 'docs_registry.json'),
                patch('openclaw.control_plane.extensions.fragment_descriptors.load_fragment_payload', side_effect=lambda _, **kwargs: kwargs) as merged,
            ):
                first = docs_registry.load_repository_registry(roots[0])
                second = docs_registry.load_repository_registry(roots[1])
                self.assertNotEqual(first['path'], second['path'])
                self.assertEqual(merged.call_count, 2)
                self.assertEqual(len(first['extensions']), 1)
        docs_registry.load_repository_registry.cache_clear()

    def test_explicit_profile_wins_over_repository_batch_and_scope_restores(self) -> None:
        with patch.object(docs_registry, 'load_registry', return_value={'mode': 'active'}), patch.object(docs_registry, 'load_repository_registry', return_value={'mode': 'repository'}):
            with docs_registry.repository_registry_scope():
                self.assertEqual(docs_registry.load_check_registry(Path('service.json')), {'mode': 'repository'})
                self.assertEqual(docs_registry.load_check_registry(Path('service.json'), explicit_config=True), {'mode': 'active'})
            self.assertEqual(docs_registry.load_check_registry(Path('service.json')), {'mode': 'active'})

    def test_repository_loader_includes_extension_disabled_in_platform_profile(self) -> None:
        docs_registry.load_repository_registry.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension_config = root / 'agent/extensions/agent_example/service.json'
            extension = SimpleNamespace(default_service_config_path=extension_config)
            platform = {'id': 'agent_platform', 'governanceSurfaces': {}}
            business = {'id': 'agent_example', 'governanceSurfaces': {'docsRegistryPath': extension_config.parent / 'docs.json'}}
            def context(config: Path) -> dict[str, object]:
                return {'extensions': [platform, business] if config == extension_config else [platform]}
            with (
                patch('openclaw.lib.repo.managed_extensions.managed_explicit_extensions', return_value=(extension,)),
                patch('openclaw.control_plane.registry_loader.config.load_registry_service_context', side_effect=context),
                patch('openclaw.lib.repo.contracts.repo_contract_path', return_value=root / 'docs_registry.json'),
                patch('openclaw.control_plane.extensions.fragment_descriptors.load_fragment_payload', side_effect=lambda _, **kwargs: kwargs),
            ):
                loaded = docs_registry.load_repository_registry(root)
            self.assertEqual([row['id'] for row in loaded['extensions']], ['agent_example', 'agent_platform'])
        docs_registry.load_repository_registry.cache_clear()


if __name__ == '__main__':
    unittest.main()
