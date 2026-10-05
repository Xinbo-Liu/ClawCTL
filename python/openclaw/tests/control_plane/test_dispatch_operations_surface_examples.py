from __future__ import annotations

import json
import re
import shlex
import unittest
from pathlib import Path

from openclaw.control_plane.cli import build_group_parser
from openclaw.control_plane.manifest_fields import DISPATCH_TARGET_REGISTRY_PATHS_KEY
from openclaw.control_plane.registry import (
    SCHEDULER_SERVICE_EXEC,
    load_registry,
    resolve_dispatch_target_operation_command,
)
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.repo.managed_extensions import load_managed_extensions_index


ROOT_DIR = resolve_repo_root(Path(__file__))
SURFACE_PATH = ROOT_DIR / 'config' / 'control_plane' / 'extensions.d' / 'agent_platform.dispatch_operations_surface.json'
OBSERVABILITY_SURFACE_PATH = ROOT_DIR / 'config' / 'governance' / 'docs' / 'dispatch_observability_surface.json'


def _command_segments(command: str) -> list[list[str]]:
    tokens = shlex.split(command)
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == '&&':
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _target_operation_invocations(command: str) -> list[tuple[str, str, str, list[str]]]:
    invocations: list[tuple[str, str, str, list[str]]] = []
    marker = ['dispatch', 'ops', 'run-target-operation']
    for segment in _command_segments(command):
        try:
            marker_index = next(
                index
                for index in range(len(segment) - len(marker) + 1)
                if segment[index:index + len(marker)] == marker
            )
        except StopIteration:
            continue
        args = segment[marker_index + len(marker):]
        passthrough_index = args.index('--') if '--' in args else len(args)
        wrapper_args = args[:passthrough_index]
        passthrough = args[passthrough_index + 1:] if passthrough_index < len(args) else []
        values: dict[str, str] = {}
        index = 0
        while index < len(wrapper_args):
            flag = wrapper_args[index]
            if not flag.startswith('--') or index + 1 >= len(wrapper_args):
                raise AssertionError(f'无法解析 target operation 参数：{segment}')
            values[flag] = wrapper_args[index + 1]
            index += 2
        operation = values.get('--operation', '')
        target = values.get('--target', '')
        profile = values.get('--control-plane-profile', '')
        if not operation or not target:
            raise AssertionError(f'target operation 缺少 operation 或 target：{segment}')
        parser_args = ['run-target-operation', '--operation', operation, '--dispatch-target-id', target]
        if profile:
            parser_args.extend(['--control-plane-profile', profile])
        if passthrough:
            parser_args.extend(['--', *passthrough])
        parsed = build_group_parser('runtime').parse_args(parser_args)
        invocations.append((
            str(parsed.operation),
            str(parsed.dispatch_target_id),
            str(parsed.control_plane_profile or ''),
            passthrough,
        ))
    return invocations


def _dispatch_target_contexts() -> list[tuple[dict[str, object], str]]:
    contexts: list[tuple[dict[str, object], str]] = []
    for extension in load_managed_extensions_index(ROOT_DIR):
        registry = load_registry(extension.default_service_config_path)
        registry_paths = dict(registry.get('registryPaths') or {})
        for path_text in list(registry_paths.get(DISPATCH_TARGET_REGISTRY_PATHS_KEY) or []):
            payload = json.loads(Path(path_text).read_text(encoding='utf-8'))
            for target in list(payload.get('targets') or []):
                if isinstance(target, dict) and str(target.get('id') or '').strip():
                    contexts.append((registry, str(target['id'])))
    return contexts


def _surface_commands(payload: dict[str, object]) -> list[str]:
    commands: list[str] = []
    for entry in dict(payload.get('entries') or {}).values():
        if not isinstance(entry, dict):
            continue
        commands.extend(str(item) for item in list(entry.get('steps') or []))
        commands.extend(
            str(item.get('command') or '')
            for item in list(entry.get('example_commands') or [])
            if isinstance(item, dict)
        )
    return commands


def _surface_references(payload: dict[str, object]) -> list[str]:
    references: list[str] = []
    for entry in dict(payload.get('entries') or {}).values():
        if isinstance(entry, dict):
            references.extend(str(item) for item in list(entry.get('references') or []))
    return references


class DispatchOperationsSurfaceExamplesTest(unittest.TestCase):
    def test_declared_target_operations_resolve_to_binding_and_cli(self) -> None:
        payload = json.loads(SURFACE_PATH.read_text(encoding='utf-8'))
        invocations = [
            invocation
            for command in _surface_commands(payload)
            for invocation in _target_operation_invocations(command)
        ]
        target_contexts = _dispatch_target_contexts()

        self.assertTrue(invocations)
        self.assertTrue(target_contexts)
        for operation, _template_target, _template_profile, extra_args in invocations:
            for registry, target_id in target_contexts:
                with self.subTest(operation=operation, target=target_id):
                    command = resolve_dispatch_target_operation_command(
                        registry,
                        dispatch_target_id=target_id,
                        operation=operation,
                        extra_args=extra_args,
                        exec_mode=SCHEDULER_SERVICE_EXEC,
                    )
                    self.assertTrue(command)
        dry_run_entry = payload['entries']['dispatch_target_default_dry_run']
        dry_run_operations = {
            operation
            for command in list(dry_run_entry.get('steps') or [])
            for operation, _target, _profile, _extra_args in _target_operation_invocations(str(command))
        }
        self.assertEqual(dry_run_operations, {'send', 'retry'})

    def test_step_commands_are_not_adjacent_duplicates(self) -> None:
        payload = json.loads(SURFACE_PATH.read_text(encoding='utf-8'))
        for entry_id, entry in payload['entries'].items():
            steps = [str(item) for item in list(entry.get('steps') or [])]
            with self.subTest(entry=entry_id):
                for left, right in zip(steps, steps[1:]):
                    self.assertNotEqual(left, right)

    def test_document_references_and_explicit_anchors_exist(self) -> None:
        payloads = [
            json.loads(SURFACE_PATH.read_text(encoding='utf-8')),
            json.loads(OBSERVABILITY_SURFACE_PATH.read_text(encoding='utf-8')),
        ]
        for reference in [item for payload in payloads for item in _surface_references(payload)]:
            path_text, separator, anchor = reference.partition('#')
            if not path_text.endswith('.md'):
                continue
            path = ROOT_DIR / path_text
            with self.subTest(reference=reference):
                self.assertTrue(path.is_file(), f'文档不存在：{reference}')
                if separator:
                    content = path.read_text(encoding='utf-8')
                    self.assertRegex(content, rf'<a\s+id=["\']{re.escape(anchor)}["\']\s*></a>')


if __name__ == '__main__':
    unittest.main()
