from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openclaw.control_plane.modules.lifecycle import (
    _collect_drop_files,
    _is_agents_boilerplate,
    _is_constraints_boilerplate,
    _is_contracts_boilerplate,
    _is_docs_boilerplate,
    _is_tests_boilerplate,
    find_external_module_references,
)
from openclaw.control_plane.modules.scaffold_support import (
    agents_md_template,
    boilerplate_surface_marker,
    constraints_readme_template,
    contracts_readme_template,
    docs_readme_template,
    tests_readme_template,
)
from openclaw.doctor.agent_modules.managed_probe_fixture import PROBE_EXTENSION_ID
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class ModuleLifecycleRegressionTest(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def _minimal_module_payload(self, repo_root: Path, *, module_ref: str, agent_ref: str) -> dict[str, object]:
        module_dir = repo_root / 'agent' / 'modules' / module_ref
        source_path = module_dir / 'module.json'
        main_path = repo_root / 'python' / 'modules' / module_ref / 'main.py'
        launcher_path = module_dir / 'bin' / module_ref
        readme_path = module_dir / 'README.md'
        skills_path = module_dir / 'skills.md'
        permissions_path = module_dir / 'permissions.json'
        tools_path = module_dir / 'tools.json'
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_text('from __future__ import annotations\n', encoding='utf-8')
        launcher_path.parent.mkdir(parents=True, exist_ok=True)
        launcher_path.write_text('#!/usr/bin/env bash\n', encoding='utf-8')
        readme_path.write_text(f'# {module_ref}\n', encoding='utf-8')
        skills_path.write_text(f'# {module_ref} skills\n', encoding='utf-8')
        permissions_path.write_text('{}\n', encoding='utf-8')
        tools_path.write_text('{}\n', encoding='utf-8')
        payload: dict[str, object] = {
            'id': module_ref,
            'agentRef': agent_ref,
            'sourcePath': str(source_path.resolve()),
            'assets': {
                'binPath': f'bin/{module_ref}',
                'readmePath': 'README.md',
                'skillsPath': 'skills.md',
                'permissionsPath': 'permissions.json',
                'toolsPath': 'tools.json',
            },
            'logic': {
                'sourcePaths': [Path(os.path.relpath(main_path.resolve(), module_dir.resolve())).as_posix()],
                'implementationRef': f'{module_ref}_impl',
            },
            'assembly': {
                'skillSetRef': f'{module_ref}_skillset',
                'permissionPolicyRef': f'{module_ref}_permissions',
                'toolsetRef': f'{module_ref}_toolset',
            },
            'controlPlane': {
                'implementation': {
                    'runtime': {
                        'config': {
                            'module': f'tests.{module_ref}.main',
                        }
                    }
                }
            },
        }
        self._write_json(source_path, payload)
        return payload

    def _module_payload(self, module_ref: str = 'alpha_probe') -> dict[str, object]:
        return {
            'id': module_ref,
            'sourcePath': str(
                (
                    ROOT_DIR
                    / 'agent'
                    / 'extensions'
                    / PROBE_EXTENSION_ID
                    / 'agent'
                    / 'modules'
                    / module_ref
                    / 'module.json'
                ).resolve()
            ),
            'assets': {
                'binPath': f'bin/{module_ref}',
            },
        }

    def test_collect_drop_files_skips_shared_logic_source_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            alpha_payload = self._minimal_module_payload(repo_root, module_ref='alpha_module', agent_ref='alpha_agent')
            beta_payload = self._minimal_module_payload(repo_root, module_ref='beta_module', agent_ref='beta_agent')
            shared_dir = repo_root / 'python' / 'modules' / 'shared'
            shared_dir.mkdir(parents=True, exist_ok=True)
            shared_file = shared_dir / 'runtime_layout.py'
            shared_file.write_text('from __future__ import annotations\n', encoding='utf-8')
            for payload in (alpha_payload, beta_payload):
                source_path = Path(str(payload['sourcePath'])).resolve()
                module_dir = source_path.parent
                logic = payload.get('logic')
                assert isinstance(logic, dict)
                source_paths = logic.get('sourcePaths')
                assert isinstance(source_paths, list)
                source_paths.append(Path(os.path.relpath(shared_dir.resolve(), module_dir.resolve())).as_posix())
                self._write_json(source_path, payload)

            registry = {
                'agentModules': [alpha_payload, beta_payload],
                'agentModulesById': {
                    'alpha_module': alpha_payload,
                    'beta_module': beta_payload,
                },
            }

            drop_files, _ = _collect_drop_files(
                repo_root,
                alpha_payload,
                registry=registry,
            )

            self.assertIn((repo_root / 'python' / 'modules' / 'alpha_module' / 'main.py').resolve(), drop_files)
            self.assertNotIn(shared_file.resolve(), drop_files)

    def test_find_external_module_references_ignores_unstructured_repo_mentions(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            module_payload = self._minimal_module_payload(
                repo_root,
                module_ref='alpha_module',
                agent_ref='dispatch_agent',
            )
            notes_path = repo_root / 'docs' / 'notes.md'
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text('alpha_module dispatch_agent\n', encoding='utf-8')
            registry = {
                'agentModules': [module_payload],
                'agentModulesById': {'alpha_module': module_payload},
                'jobs': [],
                'agentGroups': [],
            }

            refs = find_external_module_references(repo_root, module_payload, registry=registry)

            self.assertEqual(refs, [])

    def test_find_external_module_references_reports_structured_job_agent_refs(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            module_payload = self._minimal_module_payload(
                repo_root,
                module_ref='alpha_module',
                agent_ref='dispatch_agent',
            )
            job_path = repo_root / 'jobs' / 'dispatch_job.json'
            self._write_json(job_path, {'id': 'dispatch_job', 'agentRef': 'dispatch_agent'})
            registry = {
                'agentModules': [module_payload],
                'agentModulesById': {'alpha_module': module_payload},
                'jobs': [
                    {
                        'id': 'dispatch_job',
                        'agentRef': 'dispatch_agent',
                        'sourcePath': str(job_path.resolve()),
                    }
                ],
                'agentGroups': [],
            }

            refs = find_external_module_references(repo_root, module_payload, registry=registry)

            self.assertEqual(refs, ['jobs/dispatch_job.json: agentRef -> dispatch_agent'])

    def test_find_external_module_references_reports_string_depends_on_refs(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            module_payload = self._minimal_module_payload(
                repo_root,
                module_ref='alpha_module',
                agent_ref='dispatch_agent',
            )
            module_payload['operations'] = {
                'run_default': {
                    'summary': 'run default',
                    'executor': {
                        'kind': 'python_cli',
                        'argv': ['python', '-m', 'alpha_module'],
                    },
                    'jobBindings': {
                        'dispatch_job': {},
                    },
                }
            }
            job_path = repo_root / 'jobs' / 'consumer.json'
            self._write_json(job_path, {'id': 'consumer', 'dependsOn': ['dispatch_job']})
            registry = {
                'agentModules': [module_payload],
                'agentModulesById': {'alpha_module': module_payload},
                'jobs': [
                    {
                        'id': 'consumer',
                        'dependsOn': ['dispatch_job'],
                        'sourcePath': str(job_path.resolve()),
                    }
                ],
                'agentGroups': [],
            }

            refs = find_external_module_references(repo_root, module_payload, registry=registry)

            self.assertEqual(refs, ['jobs/consumer.json: dependsOn[0] -> dispatch_job'])

    def test_managed_agents_template_is_prunable_boilerplate(self) -> None:
        module_payload = self._module_payload()
        template = agents_md_template(
            'alpha_probe',
            launcher_display_path=f'agent/extensions/{PROBE_EXTENSION_ID}/agent/modules/alpha_probe/bin/alpha_probe',
        )

        self.assertTrue(_is_agents_boilerplate(module_payload, template))

    def test_customized_managed_agents_template_is_not_prunable_boilerplate(self) -> None:
        module_payload = self._module_payload()
        template = '\n'.join([
            '# alpha_probe 协作约定',
            '',
            boilerplate_surface_marker('agents'),
            '',
            '- 统一通过 `agent/extensions/agent_probe/agent/modules/alpha_probe/bin/alpha_probe` 调用。',
            '- 变更逻辑、skill、permission、tool 时，必须同步更新 `module.json`，不在会话中引入第二份实现路径。',
            '',
        ])

        self.assertFalse(_is_agents_boilerplate(module_payload, template))

    def test_managed_agents_template_with_user_content_is_not_prunable(self) -> None:
        module_payload = self._module_payload()
        template = agents_md_template(
            'alpha_probe',
            launcher_display_path=f'agent/extensions/{PROBE_EXTENSION_ID}/agent/modules/alpha_probe/bin/alpha_probe',
        )
        edited = template + '\n## Operator Notes\n\nKeep this module pinned to weekday probe runs.\n'

        self.assertFalse(_is_agents_boilerplate(module_payload, edited))

    def test_contracts_templates_use_template_equivalence(self) -> None:
        module_payload = self._module_payload()
        self.assertTrue(_is_contracts_boilerplate(module_payload, contracts_readme_template('alpha_probe')))
        self.assertFalse(
            _is_contracts_boilerplate(
                module_payload,
                '\n'.join([
                    '# alpha_probe contracts',
                    '',
                    boilerplate_surface_marker('contracts'),
                    '',
                    '单模块合同真源：`module.json -> contract`',
                    '',
                ]),
            )
        )
        self.assertFalse(
            _is_contracts_boilerplate(
                module_payload,
                contracts_readme_template('alpha_probe') + '\n额外补充：保留人工审核步骤。\n',
            )
        )

    def test_constraints_templates_use_template_equivalence(self) -> None:
        module_payload = self._module_payload()
        self.assertTrue(_is_constraints_boilerplate(module_payload, constraints_readme_template('alpha_probe')))
        self.assertFalse(
            _is_constraints_boilerplate(
                module_payload,
                '\n'.join([
                    '# alpha_probe constraints',
                    '',
                    boilerplate_surface_marker('constraints'),
                    '',
                    '局部约束、禁止事项与运行边界补充说明。',
                    '',
                ]),
            )
        )
        self.assertFalse(
            _is_constraints_boilerplate(
                module_payload,
                constraints_readme_template('alpha_probe') + '\n禁止在生产时绕过速率限制。\n',
            )
        )

    def test_docs_templates_use_template_equivalence(self) -> None:
        module_payload = self._module_payload()
        self.assertTrue(_is_docs_boilerplate(module_payload, docs_readme_template('alpha_probe')))
        self.assertFalse(
            _is_docs_boilerplate(
                module_payload,
                '\n'.join([
                    '# alpha_probe docs',
                    '',
                    boilerplate_surface_marker('docs'),
                    '',
                    '局部操作说明、变更记录与模块专属文档。',
                    '',
                ]),
            )
        )
        self.assertFalse(
            _is_docs_boilerplate(
                module_payload,
                docs_readme_template('alpha_probe') + '\n记录：2026-04-20 调整了运行顺序。\n',
            )
        )

    def test_tests_templates_use_template_equivalence(self) -> None:
        module_payload = self._module_payload()
        self.assertTrue(_is_tests_boilerplate(module_payload, tests_readme_template('alpha_probe')))
        self.assertFalse(
            _is_tests_boilerplate(
                module_payload,
                '\n'.join([
                    '# alpha_probe tests',
                    '',
                    boilerplate_surface_marker('tests'),
                    '',
                    '默认入口：`bash ./scripts/doctor/check_agent_module_smoke_tests.sh`。',
                    '',
                ]),
            )
        )
        self.assertFalse(
            _is_tests_boilerplate(
                module_payload,
                tests_readme_template('alpha_probe') + '\n增加：需要覆盖断路器回退分支。\n',
            )
        )


if __name__ == '__main__':
    unittest.main()
