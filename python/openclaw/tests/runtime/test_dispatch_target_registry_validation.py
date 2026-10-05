from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from openclaw.lib.dispatch._target_registry_validation import (
    DispatchRegistryValidationError,
    _validate_dispatch_registry_version,
    validate_dispatch_registry_payload,
)
from openclaw.lib.dispatch.target_registry import load_dispatch_registry
from openclaw.control_plane.registry_loader import load_registry_from_path
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import write_json
from openclaw.tests.support.managed_probe import managed_probe_repo


ROOT_DIR = resolve_repo_root(Path(__file__))


class DispatchTargetRegistryValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """在真实探针注册表中声明正式与验证目标，覆盖通用发布边界。"""
        super().setUpClass()
        context = managed_probe_repo('dispatch-target-boundaries', base_repo_root=ROOT_DIR)
        cls._probe = context.__enter__()
        cls.addClassCleanup(context.__exit__, None, None, None)
        cls._schema_path = cls._probe.repo_root / 'config/control_plane/schemas/dispatch_target_registry.schema.json'
        schema = json.loads(cls._schema_path.read_text(encoding='utf-8'))
        target_path = cls._probe.package_root / 'agent/control_plane/registries/dispatch_targets.json'
        payload = json.loads(target_path.read_text(encoding='utf-8'))
        formal = copy.deepcopy(payload['targets'][0])
        cls._publisher_id = 'probe_formal_publisher'
        formal['id'] = cls._publisher_id
        formal['targetGroup'] = 'biz'
        rule = schema['targetGroupBoundaryRules']['biz']
        formal['deliveryTier'] = rule['allowedDeliveryTiers'][0]
        formal['messageProfile'] = rule['allowedMessageProfiles'][0]
        formal['boundary'] = {
            'dispatchLane': rule['dispatchLane'],
            'payloadScope': rule['allowedPayloadScopes'][0],
            'completionRole': rule['completionRole'],
            'publishLatestDefault': rule['publishLatestDefault'],
            'description': '正式探针目标的发布边界。',
        }
        formal['enabledDefault'] = True
        formal['lifecycleState'] = 'active'
        formal['verificationOrderDefault'] = 5
        for key, value in formal.items():
            if key.endswith('Env') and isinstance(value, str):
                formal[key] = value.replace('PROBE_DISPATCH_', 'PROBE_FORMAL_')
        payload['targets'].insert(0, formal)
        batch = payload['verificationBatches']['batches'][0]
        batch['targetIds'].insert(0, cls._publisher_id)
        batch['requiredTargetGroups'] = sorted({row['targetGroup'] for row in payload['targets']})
        write_json(target_path, payload)
        registry = load_registry_from_path(cls._probe.service_path)
        paths = [Path(str(item)).resolve() for item in registry['registryPaths']['dispatchTargetRegistryPaths']]
        if paths != [target_path.resolve()]:
            raise AssertionError(f'探针 profile 的 dispatch target 路径不匹配：{paths}')
        cls._target_path = paths[0]
        cls._provider_paths = [
            Path(str(item)).resolve() for item in registry['registryPaths']['dispatchProviderRegistryPaths']
        ]
        if not cls._provider_paths:
            raise AssertionError('探针 profile 缺少实际 provider 注册表')

    def _current_registry_payload(self) -> tuple[dict[str, object], list[Path]]:
        payload = load_dispatch_registry(
            self._target_path,
            schema_path=self._schema_path,
            provider_registry_path=self._provider_paths,
        )
        return payload, self._provider_paths

    def test_registry_version_rejects_older_payloads(self) -> None:
        with self.assertRaises(DispatchRegistryValidationError):
            _validate_dispatch_registry_version(
                {'version': 1},
                schema_payload={'minimumRegistryVersion': 2},
            )

    def test_registry_version_accepts_minimum_supported_payload(self) -> None:
        _validate_dispatch_registry_version(
            {'version': 2},
            schema_payload={'minimumRegistryVersion': 2},
        )

    def test_current_registry_declares_target_boundaries(self) -> None:
        payload, _ = self._current_registry_payload()
        schema = json.loads(self._schema_path.read_text(encoding='utf-8'))
        rules = schema['targetGroupBoundaryRules']
        publish_latest_flags: list[bool] = []
        publisher_ids: list[str] = []

        for row in payload['targets']:
            boundary = row['boundary']
            rule = rules[row['targetGroup']]
            self.assertEqual(boundary['dispatchLane'], rule['dispatchLane'])
            self.assertIn(row['deliveryTier'], rule['allowedDeliveryTiers'])
            self.assertIn(row['messageProfile'], rule['allowedMessageProfiles'])
            self.assertIn(boundary['payloadScope'], rule['allowedPayloadScopes'])
            self.assertEqual(boundary['publishLatestDefault'], rule['publishLatestDefault'])
            self.assertEqual(boundary['completionRole'], rule['completionRole'])
            publish_latest_flags.append(bool(boundary['publishLatestDefault']))
            if boundary['publishLatestDefault']:
                publisher_ids.append(str(row['id']))

        self.assertIn(True, publish_latest_flags)
        self.assertIn(False, publish_latest_flags)
        self.assertEqual(publisher_ids, [self._publisher_id])

    def test_target_boundary_rejects_group_scope_mismatch(self) -> None:
        payload, provider_registry_paths = self._current_registry_payload()
        schema = json.loads(self._schema_path.read_text(encoding='utf-8'))
        all_scopes = set(schema['allowedPayloadScopes'])
        rules = schema['targetGroupBoundaryRules']
        broken = copy.deepcopy(payload)
        for row in broken['targets']:
            allowed_scopes = set(rules[row['targetGroup']]['allowedPayloadScopes'])
            invalid_scopes = sorted(all_scopes - allowed_scopes)
            if invalid_scopes:
                row['boundary']['payloadScope'] = invalid_scopes[0]
                break
        else:
            self.fail('schema must provide a payload scope outside at least one target group boundary')

        with self.assertRaises(DispatchRegistryValidationError):
            validate_dispatch_registry_payload(
                broken,
                provider_registry_path=provider_registry_paths,
            )

    def test_formal_latest_publisher_must_remain_active(self) -> None:
        payload, provider_registry_paths = self._current_registry_payload()
        broken = copy.deepcopy(payload)
        publisher = next(row for row in broken['targets'] if row['boundary']['publishLatestDefault'])
        publisher['lifecycleState'] = 'disabled'
        publisher['enabledDefault'] = False

        with self.assertRaisesRegex(DispatchRegistryValidationError, '必须保持 lifecycleState=active'):
            validate_dispatch_registry_payload(
                broken,
                provider_registry_path=provider_registry_paths,
            )

    def test_formal_latest_publisher_must_remain_enabled_by_default(self) -> None:
        payload, provider_registry_paths = self._current_registry_payload()
        broken = copy.deepcopy(payload)
        publisher = next(row for row in broken['targets'] if row['boundary']['publishLatestDefault'])
        publisher['enabledDefault'] = False

        with self.assertRaisesRegex(DispatchRegistryValidationError, '必须保持 enabledDefault=true'):
            validate_dispatch_registry_payload(
                broken,
                provider_registry_path=provider_registry_paths,
            )

    def test_non_formal_target_may_remain_disabled(self) -> None:
        payload, provider_registry_paths = self._current_registry_payload()
        non_formal = next(row for row in payload['targets'] if not row['boundary']['publishLatestDefault'])
        non_formal['lifecycleState'] = 'disabled'
        non_formal['enabledDefault'] = False

        validate_dispatch_registry_payload(
            payload,
            provider_registry_path=provider_registry_paths,
        )


if __name__ == '__main__':
    unittest.main()
