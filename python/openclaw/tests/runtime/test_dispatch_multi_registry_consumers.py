from __future__ import annotations

import json
import importlib
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from unittest.mock import patch

from openclaw.control_plane.dispatch import dispatch_runtime_audit
from openclaw.doctor.agent_modules.managed_probe_fixture import ManagedProbeExtensionFixture, materialize_managed_probe_extension
from openclaw.lib.channels import provider_registry
from openclaw.lib.dispatch import operations_surface
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import validate_managed_explicit_extension_index
from openclaw.setup.deploy_env.dispatch_registry import load as deploy_dispatch_load
from openclaw.tests.support.helpers import isolated_test_root


TEST_ROTATION_BATCH_ID = 'batch_default'


def _provider_registry_payload() -> dict[str, object]:
    return {
        'version': 1,
        'adapters': [{
            'id': 'alpha',
            'title': 'Alpha',
            'description': 'Alpha adapter',
            'transport': 'webhook',
            'module': 'pkg.alpha',
            'endpointValidator': 'validate',
            'payloadBuilder': 'build',
            'responseEvaluator': 'eval',
        }],
    }


def _write_provider_module(base: Path, module_name: str = 'alpha', *, package_name: str = 'pkg') -> str:
    """写入无外部通信的 provider 实现，供真实动态导入验证。"""
    package_dir = base / package_name
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / '__init__.py').write_text('', encoding='utf-8')
    (package_dir / f'{module_name}.py').write_text(
        '\n'.join([
            'def validate(*_args, **_kwargs):',
            '    return True',
            '',
            'def build(*_args, **_kwargs):',
            '    return {}',
            '',
            'def eval(*_args, **_kwargs):',
            '    return {"ok": True}',
            '',
        ]),
        encoding='utf-8',
    )
    return f'{package_name}.{module_name}'


def _dispatch_defaults() -> dict[str, object]:
    return {
        'dedupeWindowHours': 12,
        'maxAttempts': 3,
        'backoffSeconds': [30, 120],
        'targetMinIntervalMs': 0,
        'targetMaxPerSecond': 5,
        'targetMaxPerMinute': 60,
        'targetRateLimitStateTtlSeconds': 3600,
    }


def _release_policies() -> list[dict[str, object]]:
    return [{
        'id': 'review_only',
        'title': 'Review only',
        'description': 'Review-only release policy',
        'allowedReleaseLevels': ['review'],
    }]


def _lifecycle_states() -> list[dict[str, object]]:
    return [{
        'id': 'active',
        'title': 'Active',
        'description': 'Active target',
        'enableAllowed': True,
        'decommissioned': False,
    }]


def _verification_batch() -> dict[str, object]:
    return {
        'id': TEST_ROTATION_BATCH_ID,
        'title': 'Default rotation',
        'description': 'Default rotation batch',
        'requiredForRelease': False,
        'requiredTargetGroups': ['test', 'ops'],
        'targetIds': ['target_alpha', 'target_beta'],
    }


def _target_payload(
    *,
    target_id: str,
    target_group: str,
    verification_order: int,
    env_prefix: str,
    title: str,
) -> dict[str, object]:
    if target_group == 'ops':
        delivery_tier = 'technical'
        message_profile = 'ops_detail'
        boundary = {
            'dispatchLane': 'operations_monitoring',
            'payloadScope': 'ops_summary',
            'completionRole': 'required',
            'publishLatestDefault': False,
            'description': 'Ops monitoring target',
        }
    else:
        delivery_tier = 'validation'
        message_profile = 'test_detail'
        boundary = {
            'dispatchLane': 'integration_validation',
            'payloadScope': 'validation_digest',
            'completionRole': 'advisory',
            'publishLatestDefault': False,
            'description': 'Validation target',
        }
    return {
        'id': target_id,
        'transport': 'webhook',
        'provider': 'alpha',
        'targetGroup': target_group,
        'deliveryTier': delivery_tier,
        'messageProfile': message_profile,
        'enabledDefault': False,
        'silenceEnabledDefault': False,
        'silenceMinDeltaDefault': 0.0,
        'secretRequiredDefault': True,
        'endpointIsolationDefault': True,
        'atAllDefault': False,
        'formatDefault': 'text',
        'verificationOrderDefault': verification_order,
        'owner': {'team': 'ops', 'primary': 'owner-a', 'backup': 'owner-b'},
        'releasePolicyId': 'review_only',
        'lifecycleState': 'active',
        'verificationBatchIds': [TEST_ROTATION_BATCH_ID],
        'rotationClass': 'default',
        'allowedReleaseLevelsDefault': ['review'],
        'enabledEnv': f'{env_prefix}_ENABLED',
        'endpointEnv': f'{env_prefix}_ENDPOINT',
        'secretEnv': f'{env_prefix}_SECRET',
        'titleEnv': f'{env_prefix}_TITLE',
        'atAllEnv': f'{env_prefix}_AT_ALL',
        'formatEnv': f'{env_prefix}_FORMAT',
        'silenceEnabledEnv': f'{env_prefix}_SILENCE_ENABLED',
        'silenceMinDeltaEnv': f'{env_prefix}_SILENCE_MIN_DELTA',
        'allowedReleaseLevelsEnv': f'{env_prefix}_ALLOWED_LEVELS',
        'titleDefault': title,
        'boundary': boundary,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _registry_fixture(base: Path) -> tuple[Path, Path, Path]:
    providers = base / 'providers.json'
    registry_a = base / 'dispatch_a.json'
    registry_b = base / 'dispatch_b.json'
    payload = _provider_registry_payload()
    payload['adapters'][0]['module'] = _write_provider_module(base)
    _write_json(providers, payload)
    _write_json(registry_a, {
        'version': 8,
        'defaults': _dispatch_defaults(),
        'releasePolicies': _release_policies(),
        'lifecycleStates': _lifecycle_states(),
        'verificationBatches': {
            'defaultRotationBatchId': TEST_ROTATION_BATCH_ID,
            'batches': [_verification_batch()],
        },
        'targets': [
            _target_payload(
                target_id='target_alpha',
                target_group='test',
                verification_order=1,
                env_prefix='ALPHA',
                title='Alpha',
            ),
        ],
    })
    _write_json(registry_b, {
        'version': 8,
        'defaults': _dispatch_defaults(),
        'releasePolicies': _release_policies(),
        'lifecycleStates': _lifecycle_states(),
        'verificationBatches': {
            'defaultRotationBatchId': TEST_ROTATION_BATCH_ID,
            'batches': [_verification_batch()],
        },
        'targets': [
            _target_payload(
                target_id='target_beta',
                target_group='ops',
                verification_order=2,
                env_prefix='BETA',
                title='Beta',
            ),
        ],
    })
    return providers, registry_a, registry_b


def _registry_index_payload(providers: Path, registry_a: Path, registry_b: Path) -> dict[str, object]:
    return {
        'registryPaths': {
            'dispatchTargetRegistryPaths': [str(registry_a.resolve()), str(registry_b.resolve())],
            'dispatchProviderRegistryPaths': [str(providers.resolve())],
        },
        'extensions': [
            {
                'id': 'ext_alpha',
                'registry': {
                    'dispatchTargetRegistryPaths': [str(registry_a.resolve())],
                },
            },
            {
                'id': 'ext_beta',
                'registry': {
                    'dispatchTargetRegistryPaths': [str(registry_b.resolve())],
                },
            },
        ],
    }


def _managed_provider_fixture(repo_root: Path) -> tuple[ManagedProbeExtensionFixture, ManagedProbeExtensionFixture, Path, str]:
    """复用受管探针布局，建立登记 provider 的消费扩展及其 required 实现依赖。"""
    root = resolve_repo_root(Path(__file__))
    dependency = materialize_managed_probe_extension(repo_root, base_repo_root=root, extension_id='agent_probe_provider')
    consumer = materialize_managed_probe_extension(repo_root, base_repo_root=root, extension_id='agent_probe_consumer')
    package_name = f'openclaw_ext_{dependency.extension_id.removeprefix("agent_")}'
    module_name = _write_provider_module(dependency.python_root, package_name=package_name)
    # 沿用探针包的 bytecode 保护，使新增实现包仍满足受管 Python root 合同。
    (dependency.python_root / package_name / '__init__.py').write_text(
        (dependency.python_package_dir / '__init__.py').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    registry_path = consumer.package_root / 'agent/control_plane/registries/dispatch_provider_adapters.json'
    provider_payload = _provider_registry_payload()
    provider_payload['adapters'][0]['module'] = module_name
    _write_json(registry_path, provider_payload)
    for fixture in (dependency, consumer):
        manifest = json.loads(fixture.manifest_path.read_text(encoding='utf-8'))
        manifest['version'] = '1.0.0'
        manifest['registry'] = {}
        # 本场景只验证 provider 与依赖装配，不装入两个探针中同名的业务对象和产物片段。
        manifest.pop('surfaceFragments', None)
        manifest.pop('governanceSurfaces', None)
        if fixture == consumer:
            manifest['dependencies'] = [{'id': dependency.extension_id, 'version': '>=1.0.0', 'optional': False}]
            manifest['registry']['dispatchProviderRegistryPaths'] = ['@extension/agent/control_plane/registries/dispatch_provider_adapters.json']
        _write_json(fixture.manifest_path, manifest)
    service = json.loads(consumer.service_path.read_text(encoding='utf-8'))
    service['extensions']['enabledExtensionIds'] = ['agent_platform', dependency.extension_id, consumer.extension_id]
    service['extensions']['manifestsDirs'] = [
        '@repo/config/control_plane/extensions.d',
        f'@repo/{dependency.manifest_dir.relative_to(repo_root).as_posix()}',
        f'@repo/{consumer.manifest_dir.relative_to(repo_root).as_posix()}',
    ]
    _write_json(consumer.service_path, service)
    profile_registry = repo_root / 'config/control_plane/profile_registry.tsv'
    profile_registry.write_text(
        profile_registry.read_text(encoding='utf-8')
        + ''.join(f'{fixture.extension_id}\t{fixture.service_path.relative_to(repo_root).as_posix()}\n' for fixture in (dependency, consumer)),
        encoding='utf-8',
    )
    return consumer, dependency, registry_path, module_name


@contextmanager
def _isolated_provider_import_state(python_roots: tuple[Path, ...], package_name: str) -> Iterator[None]:
    """隔离合成扩展的路径和模块缓存，确保导入由被测装配入口完成并恢复原状态。"""
    original_sys_path = list(sys.path)
    removed_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == package_name or name.startswith(f'{package_name}.')
    }
    roots = {root.resolve() for root in python_roots}
    sys.path[:] = [item for item in sys.path if not str(item).strip() or Path(item).resolve() not in roots]
    importlib.invalidate_caches()
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == package_name or name.startswith(f'{package_name}.'):
                sys.modules.pop(name)
        sys.modules.update(removed_modules)
        sys.path[:] = original_sys_path
        importlib.invalidate_caches()


class DispatchMultiRegistryConsumersTest(unittest.TestCase):
    def test_collect_targets_accepts_merged_dispatch_registries(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            env_file = base / 'deploy.env'
            env_file.write_text('\n'.join([
                'ALPHA_ENABLED=true',
                'ALPHA_ENDPOINT=https://alpha.example.test/hook',
                'ALPHA_SECRET=alpha-secret',
                'BETA_ENABLED=true',
                'BETA_ENDPOINT=https://beta.example.test/hook',
                'BETA_SECRET=beta-secret',
            ]) + '\n', encoding='utf-8')
            opts = {
                'gate_env_file': str(env_file),
                'batch': TEST_ROTATION_BATCH_ID,
                'config_path': '',
            }
            try:
                with patch.object(operations_surface, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                    collected = operations_surface._collect_targets(opts)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(collected, 'target_alpha,target_beta')

    def test_target_acceptance_payload_derives_extension_from_registry_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            service_path = base / 'service.json'
            service_path.write_text('{}\n', encoding='utf-8')
            env = {
                'ALPHA_ENABLED': 'true',
                'ALPHA_ENDPOINT': 'https://alpha.example.test/hook',
                'ALPHA_SECRET': 'alpha-secret',
                'BETA_ENABLED': 'true',
                'BETA_ENDPOINT': 'https://beta.example.test/hook',
                'BETA_SECRET': 'beta-secret',
            }
            try:
                with patch.object(dispatch_runtime_audit, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                    with patch.dict(os.environ, env, clear=False):
                        payload = dispatch_runtime_audit.target_acceptance_payload('target_beta', config_path=service_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(payload['extensionId'], 'ext_beta')
        self.assertEqual(payload['target']['source_registry_path'], str(registry_b.resolve()))

    def test_load_dispatch_targets_merges_multiple_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sys.path.insert(0, str(base))
            providers, registry_a, registry_b = _registry_fixture(base)
            service_path = base / 'service.json'
            service_path.write_text('{}\n', encoding='utf-8')
            try:
                with patch.object(deploy_dispatch_load, 'require_runtime_dependencies', return_value=None):
                    with patch.object(deploy_dispatch_load, 'load_registry', return_value=_registry_index_payload(providers, registry_a, registry_b)):
                        payload = deploy_dispatch_load.load_dispatch_targets(service_path)
                        primary_path = deploy_dispatch_load.resolve_dispatch_targets_path(service_path)
            finally:
                sys.path.remove(str(base))
                sys.modules.pop('pkg.alpha', None)
                sys.modules.pop('pkg', None)

        self.assertEqual(sorted(row['id'] for row in payload['targets']), ['target_alpha', 'target_beta'])
        self.assertEqual(primary_path, registry_a.resolve())

    def test_channel_provider_registry_bootstraps_managed_dependency_python_roots(self) -> None:
        """默认 profile 与显式 registry 都应装配 owner 的 required 依赖并导入真实实现。"""
        with isolated_test_root('provider-managed-dependency') as root:
            consumer, dependency, registry_path, module_name = _managed_provider_fixture(root)
            self.assertEqual(validate_managed_explicit_extension_index(root), ())
            roots = (consumer.python_root, dependency.python_root)
            package_name = module_name.rsplit('.', 1)[0]
            for explicit in (False, True):
                with self.subTest(entry='explicit-paths' if explicit else 'default-profile'):
                    paths = [root / 'agent/control_plane/registries/dispatch_provider_adapters.json', registry_path] if explicit else None
                    env = {
                        'OPENCLAW_CONTROL_PLANE_PROFILE': 'agent_platform' if explicit else consumer.extension_id,
                        'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': '',
                        'OPENCLAW_CONTROL_PLANE_PROFILE_REGISTRY_PATH': '',
                    }
                    with _isolated_provider_import_state(roots, package_name), patch.object(provider_registry, 'ROOT_DIR', root), patch.dict(os.environ, env, clear=False):
                        self.assertNotIn(module_name, sys.modules)
                        payload = provider_registry.load_channel_provider_registry(paths)
                        spec = provider_registry.resolve_channel_provider_adapter('alpha', 'webhook', paths)
                        self.assertEqual([row['id'] for row in payload['adapters']].count('alpha'), 1)
                        self.assertIsNotNone(spec)
                        self.assertEqual(spec.module, module_name)
                        self.assertTrue(set(roots).issubset({Path(item).resolve() for item in sys.path if str(item).strip()}))
                        self.assertEqual(Path(sys.modules[module_name].__file__).resolve(), dependency.python_root / package_name / 'alpha.py')
                        self.assertTrue(provider_registry.endpoint_validator(spec)())
                        self.assertEqual(provider_registry.payload_builder(spec)(), {})
                        self.assertEqual(provider_registry.response_evaluator(spec)(), {'ok': True})

    def test_default_platform_provider_registry_keeps_managed_dependency_disabled(self) -> None:
        """平台 profile 不因受管目录存在而装入其 provider 或 required 依赖的 Python root。"""
        with isolated_test_root('provider-platform-isolation') as root:
            consumer, dependency, _, module_name = _managed_provider_fixture(root)
            roots = (consumer.python_root, dependency.python_root)
            package_name = module_name.rsplit('.', 1)[0]
            env = {
                'OPENCLAW_CONTROL_PLANE_PROFILE': 'agent_platform',
                'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH': '',
                'OPENCLAW_CONTROL_PLANE_PROFILE_REGISTRY_PATH': '',
            }
            with _isolated_provider_import_state(roots, package_name), patch.object(provider_registry, 'ROOT_DIR', root), patch.dict(os.environ, env, clear=False):
                payload = provider_registry.load_channel_provider_registry()
                self.assertNotIn('alpha', [row['id'] for row in payload['adapters']])
                self.assertIsNone(provider_registry.resolve_channel_provider_adapter('alpha', 'webhook'))
                self.assertNotIn(module_name, sys.modules)
                self.assertTrue(set(roots).isdisjoint({Path(item).resolve() for item in sys.path if str(item).strip()}))


if __name__ == '__main__':
    unittest.main()
