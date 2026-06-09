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
from openclaw.tests.support.managed_extensions import managed_extensions, representative_managed_extension


ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
PROVIDER_REGISTRY_PATH = ROOT_DIR / 'agent' / 'control_plane' / 'registries' / 'dispatch_provider_adapters.json'
SCHEMA_PATH = ROOT_DIR / 'config' / 'control_plane' / 'schemas' / 'dispatch_target_registry.schema.json'


def registry_path() -> Path:
    if not MANAGED_EXTENSIONS:
        raise unittest.SkipTest('base release surface has no repo-managed extension dispatch target registry')
    extension = representative_managed_extension(ROOT_DIR)
    registry = load_registry_from_path(extension.default_service_config_path)
    paths = [
        Path(str(item)).resolve()
        for item in (registry.get('registryPaths') or {}).get('dispatchTargetRegistryPaths') or []
    ]
    if len(paths) != 1:
        raise AssertionError(f'expected exactly one managed dispatch target registry path, got {paths}')
    return paths[0]


class DispatchTargetRegistryValidationTest(unittest.TestCase):
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
        if not MANAGED_EXTENSIONS:
            self.skipTest('base release surface has no repo-managed extension dispatch target registry')
        payload = load_dispatch_registry(registry_path(), schema_path=SCHEMA_PATH, provider_registry_path=PROVIDER_REGISTRY_PATH)
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        rules = schema['targetGroupBoundaryRules']
        publish_latest_flags: list[bool] = []

        for row in payload['targets']:
            boundary = row['boundary']
            rule = rules[row['targetGroup']]
            self.assertEqual(boundary['dispatchLane'], rule['dispatchLane'])
            self.assertIn(row['deliveryTier'], rule['allowedDeliveryTiers'])
            self.assertIn(row['messageProfile'], rule['allowedMessageProfiles'])
            self.assertIn(boundary['payloadScope'], rule['allowedPayloadScopes'])
            self.assertEqual(boundary['publishLatestDefault'], rule['publishLatestDefault'])
            publish_latest_flags.append(bool(boundary['publishLatestDefault']))

        self.assertIn(True, publish_latest_flags)
        self.assertIn(False, publish_latest_flags)

    def test_target_boundary_rejects_group_scope_mismatch(self) -> None:
        payload = load_dispatch_registry(registry_path(), schema_path=SCHEMA_PATH, provider_registry_path=PROVIDER_REGISTRY_PATH)
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
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
                provider_registry_path=PROVIDER_REGISTRY_PATH,
            )


if __name__ == '__main__':
    unittest.main()
