from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest
from unittest import mock

from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.lib.runtime.time import DEFAULT_APP_TZ
from openclaw.lib.runtime.resolver_loader import require_path_resolver
from openclaw.runtime import path_view
from openclaw.runtime import path_surface
import openclaw.runtime.generated_paths.env as generated_env
import openclaw.runtime.generated_paths.gateway.workspace as gateway_workspace
import openclaw.runtime.generated_paths.io as generated_io
from openclaw.runtime.generated_paths.env import (
    build_env_outputs,
    build_internal_api_env_output,
    env_lines,
)
from openclaw.runtime.generated_paths.gateway.config import (
    build_public_openclaw_config_output,
    load_gateway_skill_governance,
)
from openclaw.runtime.generated_paths.gateway.constants import (
    GATEWAY_AGENT_CORE_FILE_NAMES,
    GATEWAY_DEFAULT_SESSION_LABEL,
    GATEWAY_MAIN_AGENT_ID,
    GATEWAY_ROUTER_WORKSPACE_ENTRY_ID,
    GATEWAY_ROUTER_WORKSPACE_ID,
)
from openclaw.runtime.generated_paths.gateway.cron import build_gateway_cron_jobs_output
from openclaw.runtime.generated_paths.gateway.workspace import (
    gateway_agent_core_file_targets,
    gateway_agent_state_dir_targets,
    gateway_default_session_targets,
    gateway_default_session_transcript_targets,
    gateway_healthcheck_script_targets,
    gateway_router_agent_file_targets,
    gateway_router_workspace_file_targets,
    render_gateway_default_sessions,
)
from openclaw.tests.support.managed_extensions import (
    cron_jobs,
    first_model_by_provider,
    jobs_for_agent,
    managed_extensions,
    managed_extension_agent_ids,
    registry_rows,
    representative_managed_extension_registry,
)

ROOT_DIR = resolve_repo_root(Path(__file__))
MANAGED_EXTENSIONS = tuple(sorted(managed_extensions(ROOT_DIR), key=lambda row: row.id))
MANAGED_EXTENSION = MANAGED_EXTENSIONS[0] if MANAGED_EXTENSIONS else None
MANAGED_EXTENSION_CONFIG_PATH = MANAGED_EXTENSION.default_service_config_path if MANAGED_EXTENSION is not None else None
TEST_OLLAMA_BASE_URL = 'http://ollama.local:11434'
TEST_OLLAMA_MODEL_REF = 'qwen-local:32b'


@lru_cache(maxsize=1)
def _managed_registry() -> dict[str, Any]:
    return representative_managed_extension_registry(ROOT_DIR)


@lru_cache(maxsize=1)
def _default_resolver() -> Any:
    return require_path_resolver(repo_root=ROOT_DIR)


@lru_cache(maxsize=1)
def _managed_resolver() -> Any:
    return require_path_resolver(repo_root=ROOT_DIR, config_path=MANAGED_EXTENSION_CONFIG_PATH)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _agent_id(agent: dict[str, Any]) -> str:
    return _text(agent.get('id'))


def _ollama_env(model: dict[str, Any]) -> dict[str, str]:
    channel = model.get('channel') if isinstance(model.get('channel'), dict) else {}
    return {
        _text(channel.get('baseUrlEnv')): TEST_OLLAMA_BASE_URL,
        _text(model.get('modelRefEnv')): TEST_OLLAMA_MODEL_REF,
    }


def _session_key(agent_id: str) -> str:
    return f'agent:{agent_id}:{GATEWAY_DEFAULT_SESSION_LABEL}'


class RuntimePathViewTest(unittest.TestCase):
    _MANAGED_EXTENSION_REQUIRED_TESTS = {
        'test_gateway_runtime_env_exports_ollama_availability_marker',
        'test_gateway_public_config_projects_active_control_plane_agents',
        'test_gateway_public_config_projects_active_ollama_model_for_ui_chat',
        'test_runtime_paths_cli_passes_active_config_to_generated_outputs',
        'test_gateway_cron_jobs_are_display_only_projection',
        'test_gateway_cron_jobs_project_scheduler_state_for_ui',
        'test_gateway_cron_jobs_use_runtime_job_key_only',
        'test_gateway_agent_workspace_core_files_are_projected',
        'test_render_gateway_agent_state_dirs_loads_registry_from_config_path',
        'test_gateway_default_session_render_refreshes_empty_placeholder_time',
    }

    def setUp(self) -> None:
        if self._testMethodName in self._MANAGED_EXTENSION_REQUIRED_TESTS and MANAGED_EXTENSION is None:
            self.skipTest('base release surface has no repo-managed extension')

    def test_generated_paths_write_text_preserves_lf_newlines(self) -> None:
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'nested' / 'generated.txt'
            generated_io.write_text(target, 'alpha\nbeta\n')
            self.assertEqual(target.read_bytes(), b'alpha\nbeta\n')

    def test_detect_runtime_path_view_only_uses_explicit_or_state_root_signals(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                'OPENCLAW_INTERNAL_API_BASE_URL': 'https://internal.example',
                'OPENCLAW_GATEWAY_TOKEN': 'token',
                'OPENCLAW_INTERNAL_API_BIND': '0.0.0.0:8080',
                'OPENCLAW_STATE_DIR': '/tmp/state',
            },
            clear=True,
        ):
            self.assertEqual(path_view.detect_runtime_path_view(), 'host')

    def test_detect_runtime_path_view_honors_explicit_override(self) -> None:
        with mock.patch.dict(os.environ, {'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler'}, clear=True):
            self.assertEqual(path_view.detect_runtime_path_view(), 'scheduler')

    def test_generated_runtime_env_files_export_explicit_view(self) -> None:
        resolver = _default_resolver()
        self.assertIn('OPENCLAW_RUNTIME_PATH_VIEW=scheduler', env_lines('scheduler', resolver))
        self.assertIn('OPENCLAW_RUNTIME_PATH_VIEW=gateway', env_lines('gateway', resolver))
        self.assertIn('OPENCLAW_GATEWAY_STATE_DIR=/home/openclaw/.openclaw-gateway', env_lines('scheduler', resolver))
        self.assertIn('OPENCLAW_RUNTIME_PATH_VIEW=scheduler', build_internal_api_env_output(ROOT_DIR, resolver))

    def test_gateway_runtime_env_exports_ollama_availability_marker(self) -> None:
        registry = _managed_registry()
        ollama_model = first_model_by_provider(registry, 'ollama', apis={'ollama', 'ollama-chat'})
        resolver = _managed_resolver()
        with (
            mock.patch.dict(os.environ, _ollama_env(ollama_model), clear=False),
            mock.patch.object(generated_env, '_load_registry', return_value=registry),
        ):
            gateway_env = build_env_outputs(ROOT_DIR, resolver)['runtime.gateway.env']

        self.assertIn('OLLAMA_API_KEY=ollama-local', gateway_env)
        self.assertIn(f'OLLAMA_BASE_URL={TEST_OLLAMA_BASE_URL}', gateway_env)

    def test_gateway_public_config_projects_active_control_plane_agents(self) -> None:
        registry = _managed_registry()
        business_agent_ids = managed_extension_agent_ids(registry)
        resolver = _managed_resolver()
        payload = json.loads(build_public_openclaw_config_output(ROOT_DIR, resolver, MANAGED_EXTENSION_CONFIG_PATH))
        agents = payload['agents']['list']
        agent_ids = {agent['id'] for agent in agents}

        self.assertTrue(business_agent_ids)
        self.assertIn(f'workspace_{business_agent_ids[0]}', resolver.entries)
        self.assertEqual(agent_ids, {GATEWAY_MAIN_AGENT_ID, *business_agent_ids})
        self.assertEqual([agent['id'] for agent in agents], [GATEWAY_MAIN_AGENT_ID, *business_agent_ids])
        main_agent = payload['agents']['list'][0]
        self.assertTrue(main_agent.get('default') is True)
        self.assertEqual(
            main_agent['workspace'],
            resolver.resolve_entry(GATEWAY_ROUTER_WORKSPACE_ENTRY_ID)['paths']['gateway'],
        )
        self.assertEqual(main_agent['agentDir'], f'/home/node/.openclaw/agents/{GATEWAY_MAIN_AGENT_ID}/agent')
        self.assertEqual(
            main_agent['subagents'],
            {
                'allowAgents': business_agent_ids,
                'requireAgentId': True,
            },
        )
        self.assertTrue(all(agent.get('default') is not True for agent in agents[1:]))
        for agent in agents:
            self.assertEqual(agent['thinkingDefault'], 'high')
            self.assertEqual(agent['reasoningDefault'], 'stream')
            self.assertNotIn('verboseDefault', agent)
            self.assertNotIn('timeoutSeconds', agent)
        self.assertEqual(payload['agents']['defaults']['thinkingDefault'], 'high')
        self.assertNotIn('reasoningDefault', payload['agents']['defaults'])
        self.assertEqual(payload['agents']['defaults']['verboseDefault'], 'on')
        self.assertEqual(payload['agents']['defaults']['timeoutSeconds'], 1800)
        self.assertNotIn('contextTokens', payload['agents']['defaults'])
        self.assertNotIn('allowBundled', payload['skills'])
        skill_governance = load_gateway_skill_governance(ROOT_DIR)
        self.assertEqual(skill_governance['targetOpenClawVersion'], payload['meta']['lastTouchedVersion'])
        self.assertEqual(skill_governance['disabledSkills'], sorted(skill_governance['disabledSkills']))
        for skill_name in skill_governance['disabledSkills']:
            self.assertEqual(payload['skills']['entries'][skill_name], {'enabled': False})

    def test_gateway_public_config_projects_active_ollama_model_for_ui_chat(self) -> None:
        registry = _managed_registry()
        ollama_model = first_model_by_provider(registry, 'ollama', apis={'ollama', 'ollama-chat'})
        resolver = _managed_resolver()
        with mock.patch.dict(os.environ, _ollama_env(ollama_model), clear=False):
            payload = json.loads(build_public_openclaw_config_output(ROOT_DIR, resolver, MANAGED_EXTENSION_CONFIG_PATH))

        model_key = f'ollama/{TEST_OLLAMA_MODEL_REF}'
        capabilities = ollama_model.get('capabilities') if isinstance(ollama_model.get('capabilities'), dict) else {}
        self.assertEqual(payload['agents']['defaults']['model']['primary'], model_key)
        self.assertEqual(
            payload['agents']['defaults']['models'][model_key]['alias'],
            'Local Ollama',
        )
        provider = payload['models']['providers']['ollama']
        self.assertEqual(provider['baseUrl'], TEST_OLLAMA_BASE_URL)
        self.assertEqual(provider['api'], 'ollama')
        self.assertEqual(provider['apiKey'], 'ollama-local')
        self.assertTrue(provider['request']['allowPrivateNetwork'])
        self.assertEqual(payload['agents']['defaults']['sandbox']['mode'], 'all')
        self.assertIn('group:web', payload['tools']['deny'])
        self.assertIn('browser', payload['tools']['deny'])
        self.assertEqual(provider['models'][0]['id'], TEST_OLLAMA_MODEL_REF)
        self.assertEqual(provider['models'][0]['input'], ['text', 'image'] if capabilities.get('vision') else ['text'])
        self.assertEqual(provider['models'][0]['cost'], {'input': 0.0, 'output': 0.0, 'cacheRead': 0.0, 'cacheWrite': 0.0})

    def test_runtime_paths_cli_passes_active_config_to_generated_outputs(self) -> None:
        fake_resolver = mock.Mock()
        fake_resolver.absolute_host_path.side_effect = lambda entry_id: Path('/runtime') / entry_id
        with (
            mock.patch.object(path_surface, 'require_path_resolver', return_value=fake_resolver),
            mock.patch.object(path_surface, 'check_generated_outputs', return_value=0) as check_mock,
            mock.patch.object(path_surface, 'render_generated_outputs') as render_mock,
            mock.patch.object(path_surface.sys, 'stdout', io.StringIO()),
        ):
            self.assertEqual(
                path_surface.cmd_check_generated(
                    ['--repo-root', str(ROOT_DIR), '--config-path', str(MANAGED_EXTENSION_CONFIG_PATH)]
                ),
                0,
            )
            self.assertEqual(
                path_surface.cmd_render_generated(
                    ['--repo-root', str(ROOT_DIR), '--config-path', str(MANAGED_EXTENSION_CONFIG_PATH)]
                ),
                0,
            )

        check_mock.assert_called_once_with(ROOT_DIR, fake_resolver, MANAGED_EXTENSION_CONFIG_PATH)
        render_mock.assert_called_once_with(ROOT_DIR, fake_resolver, MANAGED_EXTENSION_CONFIG_PATH)

    def test_gateway_cron_jobs_are_display_only_projection(self) -> None:
        registry = _managed_registry()
        expected_jobs = cron_jobs(registry, require_agent=True)
        now = datetime(2026, 4, 27, 0, 30, tzinfo=timezone.utc)
        payload = json.loads(build_gateway_cron_jobs_output(MANAGED_EXTENSION_CONFIG_PATH, now=now))
        jobs = payload['jobs']
        job_ids = {job['id'] for job in jobs}
        first_job = expected_jobs[0]
        first_payload = jobs[0]
        first_schedule = first_job.get('schedule') if isinstance(first_job.get('schedule'), dict) else {}

        self.assertEqual(payload['version'], 1)
        self.assertEqual(job_ids, {_text(job.get('id')) for job in expected_jobs})
        self.assertEqual([job['id'] for job in jobs], [_text(job.get('id')) for job in expected_jobs])
        self.assertTrue(all(job['enabled'] is True for job in jobs))
        self.assertEqual(
            first_payload['schedule'],
            {
                'kind': _text(first_schedule.get('kind')) or 'cron',
                'expr': _text(first_schedule.get('expr')),
                'tz': _text(first_schedule.get('tz')) or DEFAULT_APP_TZ,
            },
        )
        self.assertEqual(first_payload['payload'], {'kind': 'systemEvent', 'text': 'NO_REPLY'})
        self.assertEqual(first_payload['state']['lastStatus'], 'skipped')
        self.assertIsInstance(first_payload['state']['nextRunAtMs'], int)

    def test_gateway_cron_jobs_project_scheduler_state_for_ui(self) -> None:
        first_job = cron_jobs(_managed_registry(), require_agent=True)[0]
        first_job_id = _text(first_job.get('id'))
        runtime_job_key = _text(first_job.get('resolvedRuntimeJobKey') or first_job.get('qualifiedId') or first_job_id)
        scheduler_state = {
            'jobs': {
                runtime_job_key: {
                    'currentStatus': 'succeeded',
                    'lastFinishedAt': '2026-04-27T01:05:00Z',
                    'nextScheduledRunAt': '2026-04-28T08:35:00+08:00',
                },
            },
        }
        payload = json.loads(build_gateway_cron_jobs_output(MANAGED_EXTENSION_CONFIG_PATH, scheduler_state=scheduler_state))
        job = next(item for item in payload['jobs'] if item['id'] == first_job_id)

        self.assertEqual(job['state']['lastStatus'], 'ok')
        self.assertEqual(
            job['state']['lastRunAtMs'],
            int(datetime(2026, 4, 27, 1, 5, tzinfo=timezone.utc).timestamp() * 1000),
        )
        self.assertEqual(
            job['state']['nextRunAtMs'],
            int(datetime(2026, 4, 28, 0, 35, tzinfo=timezone.utc).timestamp() * 1000),
        )

    def test_gateway_cron_jobs_use_runtime_job_key_only(self) -> None:
        first_job = cron_jobs(_managed_registry(), require_agent=True)[0]
        first_job_id = _text(first_job.get('id'))
        scheduler_state = {
            'jobs': {
                first_job_id: {
                    'currentStatus': 'succeeded',
                    'lastFinishedAt': '2026-04-27T01:05:00Z',
                    'nextScheduledRunAt': '2026-04-28T08:35:00+08:00',
                },
            },
        }
        payload = json.loads(build_gateway_cron_jobs_output(MANAGED_EXTENSION_CONFIG_PATH, scheduler_state=scheduler_state))
        job = next(item for item in payload['jobs'] if item['id'] == first_job_id)

        self.assertEqual(job['state']['lastStatus'], 'skipped')
        self.assertNotIn('lastRunAtMs', job['state'])

    def test_gateway_agent_workspace_core_files_are_projected(self) -> None:
        resolver = _managed_resolver()
        registry = _managed_registry()
        business_agent_ids = managed_extension_agent_ids(registry)
        targets = gateway_agent_core_file_targets(registry, resolver)
        router_targets = gateway_router_workspace_file_targets(ROOT_DIR, resolver)
        router_agent_targets = gateway_router_agent_file_targets(ROOT_DIR, resolver)
        healthcheck_targets = gateway_healthcheck_script_targets(ROOT_DIR, resolver)
        session_seed_time = 1777344000000
        session_targets = gateway_default_session_targets(registry, resolver, seed_time_ms=session_seed_time)
        transcript_targets = set(gateway_default_session_transcript_targets(registry, resolver))
        dir_targets = set(gateway_agent_state_dir_targets(registry, resolver))

        router_workspace = resolver.absolute_host_path(GATEWAY_ROUTER_WORKSPACE_ENTRY_ID)
        router_files = {path.name: content for path, content in router_targets.items() if path.parent == router_workspace}
        self.assertEqual(set(router_files), set(GATEWAY_AGENT_CORE_FILE_NAMES))
        self.assertIn(GATEWAY_ROUTER_WORKSPACE_ID, router_files['USER.md'])
        self.assertIn('初始路由', router_files['BOOTSTRAP.md'])
        router_agent_dir = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / GATEWAY_MAIN_AGENT_ID / 'agent'
        router_agent_files = {
            path.name: content
            for path, content in router_agent_targets.items()
            if path.parent == router_agent_dir
        }
        self.assertEqual(router_agent_files, router_files)
        self.assertIn(router_agent_dir, dir_targets)
        self.assertIn(resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / GATEWAY_MAIN_AGENT_ID / 'sessions', dir_targets)
        healthcheck_path = (
            resolver.absolute_host_path('gateway_host_state_dir')
            / 'healthchecks'
            / 'gateway-tcp-liveness.cjs'
        )
        self.assertIn(healthcheck_path, healthcheck_targets)
        self.assertIn("net.connect({ host: '127.0.0.1', port: 18789 })", healthcheck_targets[healthcheck_path])

        for agent_id in business_agent_ids:
            workspace = resolver.absolute_host_path(f'workspace_{agent_id}')
            files = {path.name: content for path, content in targets.items() if path.parent == workspace}
            agent_dir = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / agent_id / 'agent'
            agent_files = {path.name: content for path, content in targets.items() if path.parent == agent_dir}
            sessions_dir = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / agent_id / 'sessions'
            session_store = sessions_dir / 'sessions.json'
            self.assertEqual(set(files), set(GATEWAY_AGENT_CORE_FILE_NAMES))
            self.assertEqual(agent_files, files)
            self.assertIn(agent_id, files['IDENTITY.md'])
            self.assertIn('control-plane registry', files['AGENTS.md'])
            self.assertIn('runtime adapter', files['BOOTSTRAP.md'])
            self.assertIn(agent_dir, dir_targets)
            self.assertIn(sessions_dir, dir_targets)
            self.assertIn(session_store, session_targets)
            self.assertIn(_session_key(agent_id), session_targets[session_store])
            session_payload = session_targets[session_store][_session_key(agent_id)]
            self.assertEqual(session_payload['chatType'], 'direct')
            self.assertEqual(session_payload['lastChannel'], 'webchat')
            self.assertEqual(session_payload['deliveryContext'], {'channel': 'webchat'})
            self.assertEqual(session_payload['origin'], {'provider': 'webchat', 'surface': 'webchat', 'chatType': 'direct'})
            self.assertEqual(session_payload['updatedAt'], session_seed_time)
            self.assertEqual(session_payload['lastInteractionAt'], session_seed_time)
            self.assertEqual(session_payload['status'], 'done')
            self.assertFalse(session_payload['systemSent'])
            self.assertTrue(session_payload['sessionFile'].startswith(f'/home/node/.openclaw/agents/{agent_id}/sessions/'))
            self.assertIn(sessions_dir / f"{session_payload['sessionId']}.jsonl", transcript_targets)

        main_sessions_dir = resolver.absolute_host_path('gateway_host_state_dir') / 'agents' / GATEWAY_MAIN_AGENT_ID / 'sessions'
        main_session_store = main_sessions_dir / 'sessions.json'
        self.assertIn(main_session_store, session_targets)
        self.assertIn(_session_key(GATEWAY_MAIN_AGENT_ID), session_targets[main_session_store])

        sample_agent = next(agent for agent in registry_rows(registry, 'agents') if _agent_id(agent))
        sample_agent_id = _agent_id(sample_agent)
        sample_workspace = resolver.absolute_host_path(f'workspace_{sample_agent_id}')
        sample_files = {path.name: content for path, content in targets.items() if path.parent == sample_workspace}
        self.assertIn(_text(sample_agent.get('title')), sample_files['IDENTITY.md'])
        sample_jobs = jobs_for_agent(registry, sample_agent_id)
        if sample_jobs:
            self.assertIn(_text(sample_jobs[0].get('id')), sample_files['HEARTBEAT.md'])
        model_agent = next(
            (
                agent
                for agent in registry_rows(registry, 'agents')
                if isinstance(agent.get('capabilities'), dict) and 'modelRequired' in agent['capabilities']
            ),
            None,
        )
        if model_agent is not None:
            model_agent_id = _agent_id(model_agent)
            model_workspace = resolver.absolute_host_path(f'workspace_{model_agent_id}')
            model_files = {path.name: content for path, content in targets.items() if path.parent == model_workspace}
            self.assertIn('modelRequired', model_files['TOOLS.md'])

    def test_render_gateway_agent_state_dirs_loads_registry_from_config_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            target_dir = Path(tmpdir) / 'gateway' / 'agents' / 'probe'
            resolver = mock.Mock()
            resolver.config_path = MANAGED_EXTENSION_CONFIG_PATH
            with (
                mock.patch.object(gateway_workspace, '_load_registry', return_value={'agents': []}) as load_mock,
                mock.patch.object(gateway_workspace, 'gateway_agent_state_dir_targets', return_value=[target_dir]),
                mock.patch.object(gateway_workspace, 'gateway_healthcheck_script_targets', return_value={}),
                mock.patch.object(gateway_workspace, 'gateway_router_workspace_file_targets', return_value={}),
                mock.patch.object(gateway_workspace, 'gateway_router_agent_file_targets', return_value={}),
                mock.patch.object(gateway_workspace, 'gateway_agent_core_file_targets', return_value={}),
                mock.patch.object(gateway_workspace, 'render_gateway_default_sessions') as sessions_mock,
            ):
                gateway_workspace.render_gateway_agent_state_dirs(ROOT_DIR, resolver, MANAGED_EXTENSION_CONFIG_PATH)

                self.assertTrue(target_dir.is_dir())
                load_mock.assert_called_once_with(MANAGED_EXTENSION_CONFIG_PATH)
                sessions_mock.assert_called_once()

    def test_gateway_default_session_render_refreshes_empty_placeholder_time(self) -> None:
        sample_agent_id = managed_extension_agent_ids(_managed_registry())[0]
        with TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir) / 'agents' / sample_agent_id / 'sessions'
            sessions_dir.mkdir(parents=True)
            store_path = sessions_dir / 'sessions.json'
            transcript_path = sessions_dir / 'seed-session.jsonl'
            transcript_path.write_text('', encoding='utf-8')
            store_path.write_text(
                json.dumps(
                    {
                        _session_key(sample_agent_id): {
                            'sessionId': 'seed-session',
                            'updatedAt': 1,
                            'sessionStartedAt': 1,
                            'lastInteractionAt': 1,
                            'systemSent': False,
                            'abortedLastRun': False,
                            'chatType': 'direct',
                            'sessionFile': f'/home/node/.openclaw/agents/{sample_agent_id}/sessions/seed-session.jsonl',
                            'deliveryContext': {'channel': 'webchat'},
                            'lastChannel': 'webchat',
                            'status': 'done',
                            'origin': {'provider': 'webchat', 'surface': 'webchat', 'chatType': 'direct'},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            seeded_payload = {
                _session_key(sample_agent_id): {
                    'sessionId': 'seed-session',
                    'updatedAt': 1777344000000,
                    'sessionStartedAt': 1777344000000,
                    'lastInteractionAt': 1777344000000,
                    'systemSent': False,
                    'abortedLastRun': False,
                    'chatType': 'direct',
                    'sessionFile': f'/home/node/.openclaw/agents/{sample_agent_id}/sessions/seed-session.jsonl',
                    'deliveryContext': {'channel': 'webchat'},
                    'lastChannel': 'webchat',
                    'status': 'done',
                    'origin': {'provider': 'webchat', 'surface': 'webchat', 'chatType': 'direct'},
                },
            }

            with mock.patch.object(gateway_workspace, 'gateway_default_session_targets', return_value={store_path: seeded_payload}):
                with mock.patch.object(gateway_workspace, 'gateway_default_session_transcript_targets', return_value=[]):
                    render_gateway_default_sessions({}, mock.Mock())

            payload = json.loads(store_path.read_text(encoding='utf-8'))[_session_key(sample_agent_id)]
            self.assertEqual(payload['updatedAt'], 1777344000000)
            self.assertEqual(payload['sessionStartedAt'], 1777344000000)
            self.assertEqual(payload['lastInteractionAt'], 1777344000000)


if __name__ == '__main__':
    unittest.main()
