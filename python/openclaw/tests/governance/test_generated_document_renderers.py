"""验证生成文档的只读模式、受控写入和跨环境稳定性。"""
from __future__ import annotations

import contextlib
import io
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openclaw import cli_registry
from openclaw.docs.renderers import extension_index, maintenance_map, runtime_artifacts
from openclaw.docs.support import generated_output
from openclaw.docs.support.markdown_links import local_link_errors, parse_markdown, resolve_local_link
from openclaw.lib.io import state as state_io


class GeneratedDocumentSafetyTest(unittest.TestCase):
    def test_readonly_modes_preserve_document_bytes_and_identity(self) -> None:
        for renderer, target_rel in ((runtime_artifacts, runtime_artifacts.resolve_target().relative_to(runtime_artifacts.ROOT_DIR)), (maintenance_map, maintenance_map.MAINTENANCE_MAP_DOC)):
            with self.subTest(renderer=renderer.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / target_rel
                target.parent.mkdir(parents=True)
                target.write_text('人工并发内容\n', encoding='utf-8')
                original = (target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_ino)
                target_resolution = patch.object(runtime_artifacts, 'resolve_target', return_value=target) if renderer is runtime_artifacts else contextlib.nullcontext()
                with patch.object(renderer, 'ROOT_DIR', root), target_resolution, patch.object(renderer, 'render_doc', return_value='# 生成内容\n'):
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(renderer.render_entry(['--check']), 1)
                        self.assertEqual(renderer.render_entry(['--stdout']), 0)
                self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_ino), original)
                self.assertEqual(list(target.parent.glob('*.lock')), [])

    def test_output_modes_are_mutually_exclusive(self) -> None:
        for renderer in (runtime_artifacts, extension_index, maintenance_map):
            with self.subTest(renderer=renderer.__name__), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    renderer.render_entry(['--check', '--stdout'])
                self.assertEqual(caught.exception.code, 2)

    def test_lock_timeout_returns_failure_without_changing_document(self) -> None:
        for renderer, target_rel in ((runtime_artifacts, runtime_artifacts.resolve_target().relative_to(runtime_artifacts.ROOT_DIR)), (maintenance_map, maintenance_map.MAINTENANCE_MAP_DOC), (extension_index, extension_index.DOC_PATH)):
            with self.subTest(renderer=renderer.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / target_rel
                target.parent.mkdir(parents=True)
                manual = '# 原文\n' + extension_index.BEGIN_MARKER + '\n' + extension_index.END_MARKER + '\n'
                target.write_text(manual, encoding='utf-8')
                original = target.read_bytes()
                rendering = patch.object(renderer, 'render_section', return_value=extension_index.BEGIN_MARKER + '\n新内容\n' + extension_index.END_MARKER) if renderer is extension_index else patch.object(renderer, 'render_doc', return_value='# 生成文档\n')
                target_resolution = patch.object(runtime_artifacts, 'resolve_target', return_value=target) if renderer is runtime_artifacts else contextlib.nullcontext()
                stderr = io.StringIO()
                with patch.object(renderer, 'ROOT_DIR', root), target_resolution, rendering, patch.object(generated_output, 'with_lock_dir', side_effect=RuntimeError('生成锁超时')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    self.assertEqual(renderer.render_entry([]), 2)
                self.assertIn('生成锁超时', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertEqual(target.read_bytes(), original)

    def test_invalid_contract_path_returns_failure_without_changing_document(self) -> None:
        for renderer, target_rel in ((runtime_artifacts, runtime_artifacts.resolve_target().relative_to(runtime_artifacts.ROOT_DIR)), (maintenance_map, maintenance_map.MAINTENANCE_MAP_DOC)):
            with self.subTest(renderer=renderer.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / target_rel
                target.parent.mkdir(parents=True)
                target.write_text('# 保留原文\n', encoding='utf-8')
                original = target.read_bytes()
                stderr = io.StringIO()
                target_resolution = patch.object(runtime_artifacts, 'resolve_target', return_value=target) if renderer is runtime_artifacts else contextlib.nullcontext()
                with patch.object(renderer, 'ROOT_DIR', root), target_resolution, patch.object(renderer, 'render_doc', side_effect=KeyError('missing_runtime_entry')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    self.assertEqual(renderer.render_entry([]), 2)
                self.assertIn('missing_runtime_entry', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertEqual(target.read_bytes(), original)

    def test_atomic_write_rejects_content_changed_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'generated.md'
            target.write_text('原内容', encoding='utf-8')
            snapshot = generated_output.read_document(root, target)
            target.write_text('其他维护者的新内容', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '其他写入者'):
                generated_output.write_document(root, target, '生成内容', snapshot)
            self.assertEqual(target.read_text(encoding='utf-8'), '其他维护者的新内容')

    def test_default_write_can_create_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'generated.md'
            snapshot = generated_output.read_document(root, target)
            generated_output.write_document(root, target, '# 文档\n', snapshot)
            self.assertEqual(target.read_text(encoding='utf-8'), '# 文档\n')

    def test_commit_guard_rejects_edit_after_staging_and_preserves_manual_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'generated.md'
            target.write_text('原内容', encoding='utf-8')
            snapshot = generated_output.read_document(root, target)
            real_fsync = os.fsync
            intervened = False

            def after_stage(descriptor: int) -> None:
                nonlocal intervened
                real_fsync(descriptor)
                opened = os.fstat(descriptor)
                for stage in root.glob('.generated.md.*.tmp'):
                    staged = stage.stat()
                    if not intervened and (opened.st_dev, opened.st_ino) == (staged.st_dev, staged.st_ino):
                        intervened = True
                        target.write_text('暂存期间的人工内容', encoding='utf-8')

            with patch.object(state_io.os, 'fsync', side_effect=after_stage):
                with self.assertRaisesRegex(ValueError, '其他写入者'):
                    generated_output.write_document(root, target, '生成内容', snapshot)
            self.assertTrue(intervened)
            self.assertEqual(target.read_text(encoding='utf-8'), '暂存期间的人工内容')
            self.assertEqual(list(root.glob('*.tmp')), [])
            self.assertEqual(list(root.glob('*.lock')), [])

    def test_directory_fd_prevents_parent_redirect_after_staging(self) -> None:
        for redirect_to_link in (True, False):
            with self.subTest(symlink=redirect_to_link), tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
                root = Path(temporary)
                parent = root / 'docs'
                parent.mkdir()
                target = parent / 'generated.md'
                target.write_text('原内容', encoding='utf-8')
                snapshot = generated_output.read_document(root, target)
                outside = Path(external) / 'generated.md'
                outside.write_text('外部内容必须保留', encoding='utf-8')
                moved = root / 'original_docs'
                real_fsync = os.fsync
                intervened = False

                def after_stage(descriptor: int) -> None:
                    nonlocal intervened
                    real_fsync(descriptor)
                    opened = os.fstat(descriptor)
                    for stage in parent.glob('.generated.md.*.tmp'):
                        staged = stage.stat()
                        if not intervened and (opened.st_dev, opened.st_ino) == (staged.st_dev, staged.st_ino):
                            intervened = True
                            parent.rename(moved)
                            if redirect_to_link:
                                parent.symlink_to(Path(external), target_is_directory=True)
                            else:
                                parent.mkdir()
                                target.write_text('新目录中的人工内容', encoding='utf-8')

                try:
                    with patch.object(state_io.os, 'fsync', side_effect=after_stage):
                        with self.assertRaisesRegex(ValueError, '链接或 reparse point|父目录已变化'):
                            generated_output.write_document(root, target, '生成内容', snapshot)
                    self.assertTrue(intervened)
                    self.assertEqual(outside.read_text(encoding='utf-8'), '外部内容必须保留')
                    self.assertEqual((moved / 'generated.md').read_text(encoding='utf-8'), '原内容')
                    self.assertEqual(list(moved.glob('*.tmp')), [])
                    self.assertEqual(list(Path(external).glob('*.tmp')), [])
                    if not redirect_to_link:
                        self.assertEqual(target.read_text(encoding='utf-8'), '新目录中的人工内容')
                finally:
                    if parent.is_symlink():
                        parent.unlink()

    def test_lost_lock_owner_never_suppresses_guard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'generated.lock'
            with self.assertRaisesRegex(ValueError, '必须传播'):
                with state_io.with_lock_dir(lock):
                    (lock / state_io.LOCK_METADATA_NAME).write_text(json.dumps({'ownerToken': 'other_writer'}), encoding='utf-8')
                    raise ValueError('提交 guard 失败必须传播')

    def test_default_atomic_writer_keeps_original_path_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'new/ordinary.txt'
            state_io.write_text_atomic(target, '原运行写入\n')
            self.assertEqual(target.read_text(encoding='utf-8'), '原运行写入\n')
            state_io.write_text_atomic(target, '替换内容\n')
            self.assertEqual(target.read_text(encoding='utf-8'), '替换内容\n')

    def test_linked_document_is_refused_without_changing_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'original.md'
            original.write_text('保留原文', encoding='utf-8')
            linked = root / 'linked.md'
            try:
                os.link(original, linked)
            except OSError as exc:
                self.skipTest(f'当前文件系统不能建立硬链接：{exc}')
            with self.assertRaisesRegex(ValueError, '单链接普通文件'):
                generated_output.read_document(root, linked)
            self.assertEqual(original.read_text(encoding='utf-8'), '保留原文')

    def test_symlink_parent_and_outside_root_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            external = Path(outside) / 'document.md'
            external.write_text('外部文件', encoding='utf-8')
            with self.assertRaises(ValueError):
                generated_output.read_document(root, external)
            linked = root / 'redirect'
            try:
                linked.symlink_to(Path(outside), target_is_directory=True)
            except OSError as exc:
                self.skipTest(f'当前权限不能建立符号链接：{exc}')
            with self.assertRaisesRegex(ValueError, '链接或 reparse point'):
                generated_output.read_document(root, linked / external.name)
            self.assertEqual(external.read_text(encoding='utf-8'), '外部文件')


class GeneratedDocumentContentTest(unittest.TestCase):
    def test_runtime_target_and_content_follow_changed_contract_paths_and_output_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth = root / 'config/governance/support/repo_contracts.json'
            truth.parent.mkdir(parents=True)
            summary = {'schedulerRunsRoot': 'state/scheduler/runs', 'items': []}
            for revision in ('first', 'replacement'):
                object_source = f'truth/{revision}/objects.json'
                policy_source = f'truth/{revision}/policy.json'
                registry_source = f'truth/{revision}/registry.json'
                target_relative = f'docs/{revision}/generated.md'
                truth.write_text(json.dumps({'contracts': [
                    {'id': 'control_plane.object_families', 'relative_path': object_source, 'format': 'json'},
                    {'id': 'control_plane.job_artifact_policy_surface', 'relative_path': policy_source, 'format': 'json'},
                    {'id': 'governance.docs_registry', 'relative_path': registry_source, 'format': 'json'},
                ]}), encoding='utf-8')
                object_path = root / object_source
                object_path.parent.mkdir(parents=True)
                object_path.write_text(json.dumps({'generated_artifacts': {'object_family_doc': target_relative}, 'families': {revision: {'label': f'真源对象族 {revision}', 'purpose': '读取登记的当前配置', 'entries': [{'id': 'artifact', 'path_kind': 'repo_relative', 'path_ref': 'output/reference.json', 'producer': 'fixture', 'usage': '验证真源路径'}]}}}), encoding='utf-8')
                (root / policy_source).write_text(json.dumps({'generated_artifacts': {'artifact_policy_doc': target_relative}}), encoding='utf-8')
                reference_pages = [
                    ('manual-post-deploy-checks', f'references/{revision}/runtime.md'),
                    ('maintenance_facts_overview', f'架构/{revision}/维护 地图.md'),
                    ('control-ui-first-pairing', f'guides/{revision}/failures.md'),
                ]
                (root / registry_source).write_text(json.dumps({'pages': [{'path': path, 'entrypointContract': {'requiredRefs': [{'kind': 'literal', 'id': identity}]}} for identity, path in reference_pages]}), encoding='utf-8')
                for _, path in reference_pages:
                    reference = root / path
                    reference.parent.mkdir(parents=True, exist_ok=True)
                    reference.write_text('# 导航目标\n', encoding='utf-8')
                target = root / target_relative
                target.parent.mkdir(parents=True)
                target.write_text('人工原文', encoding='utf-8')
                original = (target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_ino)
                stdout = io.StringIO()
                with self.subTest(revision=revision), patch.object(runtime_artifacts, 'ROOT_DIR', root), patch.object(runtime_artifacts, 'build_summary', return_value=summary), patch('openclaw.control_plane.extensions.descriptors.core.iter_extension_fragment_paths', return_value=[]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(runtime_artifacts.resolve_target(root_dir=root), target)
                    self.assertEqual(runtime_artifacts.render_entry(['--stdout']), 0)
                self.assertIn(f'真源对象族 {revision}', stdout.getvalue())
                self.assertIn('output/reference.json', stdout.getvalue())
                self.assertEqual(local_link_errors(target, stdout.getvalue(), root_dir=root), [])
                self.assertEqual({resolve_local_link(target, link.target, root_dir=root)[0] for link in parse_markdown(stdout.getvalue()).links}, {(root / path).resolve() for _, path in reference_pages})
                self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns, target.stat().st_ino), original)

    def test_runtime_contract_sources_reject_symlinks_outside_repository_before_rendering(self) -> None:
        for contract_id, source in (('control_plane.object_families', 'objects.json'), ('control_plane.job_artifact_policy_surface', 'policy.json')):
            with self.subTest(contract=contract_id), tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
                root = Path(temporary)
                truth = root / 'config/governance/support/repo_contracts.json'
                truth.parent.mkdir(parents=True)
                truth.write_text(json.dumps({'contracts': [
                    {'id': 'control_plane.object_families', 'relative_path': 'objects.json', 'format': 'json'},
                    {'id': 'control_plane.job_artifact_policy_surface', 'relative_path': 'policy.json', 'format': 'json'},
                ]}), encoding='utf-8')
                (root / 'objects.json').write_text(json.dumps({'generated_artifacts': {'object_family_doc': 'docs/generated.md'}}), encoding='utf-8')
                (root / 'policy.json').write_text(json.dumps({'generated_artifacts': {'artifact_policy_doc': 'docs/generated.md'}}), encoding='utf-8')
                external = Path(outside) / 'external.json'
                external.write_text((root / source).read_text(encoding='utf-8'), encoding='utf-8')
                (root / source).unlink()
                try:
                    (root / source).symlink_to(external)
                except OSError as exc:
                    self.skipTest(f'当前权限不能建立符号链接：{exc}')
                target = root / 'docs/generated.md'
                target.parent.mkdir()
                target.write_text('保留人工原文', encoding='utf-8')
                original = target.read_bytes()
                stderr = io.StringIO()
                with patch.object(runtime_artifacts, 'ROOT_DIR', root), patch.object(runtime_artifacts, 'render_doc', side_effect=AssertionError('越界真源必须在渲染前拒绝')), contextlib.redirect_stderr(stderr):
                    self.assertEqual(runtime_artifacts.render_entry(['--stdout']), 2)
                self.assertIn(f'repo contract {contract_id} 解析出了仓库根之外的路径', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertEqual(target.read_bytes(), original)

    def test_runtime_navigation_rejects_missing_or_ambiguous_page_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth = root / 'config/governance/support/repo_contracts.json'
            truth.parent.mkdir(parents=True)
            truth.write_text(json.dumps({'contracts': [{'id': 'governance.docs_registry', 'relative_path': 'registry.json', 'format': 'json'}]}), encoding='utf-8')
            for count in (0, 2):
                (root / 'registry.json').write_text(json.dumps({'pages': [{'path': f'guides/runtime-{index}.md', 'entrypointContract': {'requiredRefs': [{'kind': 'literal', 'id': 'manual-post-deploy-checks'}]}} for index in range(count)]}), encoding='utf-8')
                with self.subTest(matches=count), patch('openclaw.control_plane.extensions.descriptors.core.iter_extension_fragment_paths', return_value=[]):
                    with self.assertRaisesRegex(ValueError, f'manual-post-deploy-checks.*匹配 {count} 项'):
                        runtime_artifacts._navigation_line(root, root / 'docs/generated.md')

    def test_runtime_target_disagreement_reports_both_true_sources_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth = root / 'config/governance/support/repo_contracts.json'
            truth.parent.mkdir(parents=True)
            truth.write_text(json.dumps({'contracts': [
                {'id': 'control_plane.object_families', 'relative_path': 'objects.json', 'format': 'json'},
                {'id': 'control_plane.job_artifact_policy_surface', 'relative_path': 'policy.json', 'format': 'json'},
            ]}), encoding='utf-8')
            (root / 'objects.json').write_text(json.dumps({'generated_artifacts': {'object_family_doc': 'docs/first.md'}}), encoding='utf-8')
            (root / 'policy.json').write_text(json.dumps({'generated_artifacts': {'artifact_policy_doc': 'docs/second.md'}}), encoding='utf-8')
            stderr = io.StringIO()
            with patch.object(runtime_artifacts, 'ROOT_DIR', root), patch.object(runtime_artifacts, 'render_doc', side_effect=AssertionError('目标冲突应在渲染前失败')), contextlib.redirect_stderr(stderr):
                self.assertEqual(runtime_artifacts.render_entry(['--stdout']), 2)
            self.assertIn('objects.json 声明 docs/first.md', stderr.getvalue())
            self.assertIn('policy.json 声明 docs/second.md', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())
            self.assertFalse((root / 'docs').exists())

    def test_missing_runtime_target_is_reported_without_traceback_or_output_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth = root / 'config/governance/support/repo_contracts.json'
            truth.parent.mkdir(parents=True)
            truth.write_text(json.dumps({'contracts': [
                {'id': 'control_plane.object_families', 'relative_path': 'objects.json', 'format': 'json'},
                {'id': 'control_plane.job_artifact_policy_surface', 'relative_path': 'policy.json', 'format': 'json'},
            ]}), encoding='utf-8')
            (root / 'objects.json').write_text('{}', encoding='utf-8')
            (root / 'policy.json').write_text('{}', encoding='utf-8')
            stderr = io.StringIO()
            with patch.object(runtime_artifacts, 'ROOT_DIR', root), contextlib.redirect_stderr(stderr):
                self.assertEqual(runtime_artifacts.render_entry(['--stdout']), 2)
            self.assertIn('object_family_doc', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())
            self.assertFalse((root / 'docs').exists())

    def test_extension_markers_preserve_manual_text_and_reject_duplicates(self) -> None:
        before, after = '# 人工入口\n\n', '\n\n## 人工维护\n'
        content = before + extension_index.BEGIN_MARKER + '\n旧区块\n' + extension_index.END_MARKER + after
        section = extension_index.BEGIN_MARKER + '\n新索引\n' + extension_index.END_MARKER
        self.assertEqual(extension_index.replace_section(content, section), before + section + after)
        for invalid in (content + extension_index.END_MARKER, content.replace(extension_index.BEGIN_MARKER, ''), content.replace(extension_index.BEGIN_MARKER, '前缀 ' + extension_index.BEGIN_MARKER)):
            with self.subTest(content=invalid), self.assertRaises(ValueError):
                extension_index.replace_section(invalid, section)

    def test_extension_index_uses_real_names_and_local_readme_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extension_root = root / 'agent/extensions/agent_sample'
            extension_root.mkdir(parents=True)
            (extension_root / 'README.md').write_text('# 样例扩展', encoding='utf-8')
            row = Mock(id='agent_sample', title='样例扩展', root_dir=extension_root)
            with patch.object(extension_index, 'managed_explicit_extensions', return_value=[row]):
                section = extension_index.render_section(root_dir=root)
                self.assertIn('`agent_sample`', section)
                self.assertIn('样例扩展', section)
                self.assertIn('[README](agent_sample/README.md)', section)
                (extension_root / 'README.md').unlink()
                with self.assertRaisesRegex(ValueError, '缺少普通 README'):
                    extension_index.render_section(root_dir=root)

    def test_extension_readonly_drift_does_not_write_manual_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / extension_index.DOC_PATH
            target.parent.mkdir(parents=True)
            target.write_text('# 人工内容\n' + extension_index.BEGIN_MARKER + '\n' + extension_index.END_MARKER + '\n', encoding='utf-8')
            original = target.read_bytes()
            section = extension_index.BEGIN_MARKER + '\n新索引\n' + extension_index.END_MARKER
            with patch.object(extension_index, 'ROOT_DIR', root), patch.object(extension_index, 'render_section', return_value=section), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(extension_index.render_entry(['--check']), 1)
                self.assertEqual(extension_index.render_entry(['--stdout']), 0)
            self.assertEqual(target.read_bytes(), original)

    def test_runtime_artifacts_use_canonical_platform_and_posix_summary_paths(self) -> None:
        summary = {'schedulerRunsRoot': 'state\\openclaw\\scheduler\\runs', 'generatedAt': 'volatile', 'configPath': 'C:\\private\\config.json', 'items': [{'id': 'test_job', 'runArtifactRootEntry': 'test_root', 'latestAlias': 'latest', 'retentionDays': 0, 'schedulerRunManifestPathPattern': 'state\\openclaw\\scheduler\\runs\\test_job\\<run_id>\\run.json'}]}
        with patch.object(runtime_artifacts, 'build_summary', return_value=summary) as build_summary, patch.dict(os.environ, {'OPENCLAW_CONTROL_PLANE_PROFILE': 'unselected_business', 'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler', 'OPENCLAW_STATE_DIR': '/private/host/state'}):
            rendered = runtime_artifacts.render_doc()
        self.assertEqual(build_summary.call_args.kwargs['config_path'], runtime_artifacts.ROOT_DIR / 'config/control_plane/profiles/agent_platform.service.json')
        self.assertEqual(build_summary.call_args.kwargs['base_root'], runtime_artifacts.ROOT_DIR)
        self.assertNotIn('path_resolver', build_summary.call_args.kwargs)
        self.assertIn('state/openclaw/scheduler/runs/test_job/<run_id>/run.json', rendered)
        self.assertIn('state/openclaw/scheduler/runs', rendered)
        for volatile in ('generatedAt', 'volatile', 'C:\\private', '/private/host/state', 'unselected_business'):
            self.assertNotIn(volatile, rendered)

    def test_new_renderer_commands_are_registered(self) -> None:
        tree = cli_registry.root_command_tree(runtime_artifacts.ROOT_DIR / 'config/control_plane/profiles/agent_platform.service.json')
        self.assertEqual(tree['docs']['render-runtime-artifacts'], 'openclaw.docs.renderers.runtime_artifacts:render_entry')
        self.assertEqual(tree['docs']['render-extension-index'], 'openclaw.docs.renderers.extension_index:render_entry')


if __name__ == '__main__':
    unittest.main()
