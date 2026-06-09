from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw.control_plane import state_paths
from openclaw.control_plane.api import access
from openclaw.control_plane.api import agent_group_release
from openclaw.control_plane import run_ledger
from openclaw.lib.testing.acceptance import summary as acceptance_summary
from openclaw.lib.control_plane import object_families
import openclaw.control_plane.api.summary_builders as summary_builders


class SummaryBuildersTest(unittest.TestCase):
    def test_control_plane_state_root_uses_host_control_plane_dir_on_host_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            expected = (Path(tmpdir) / 'control_plane').resolve()

            class FakeResolver:
                def resolve_path(self, entry_id: str, view: str = 'host', env: dict[str, str] | None = None) -> str:
                    if entry_id != 'control_plane_host_state_dir' or view != 'host':
                        raise AssertionError(f'unexpected path request: {entry_id}/{view}')
                    return str(expected)

            with patch.dict(os.environ, {'OPENCLAW_RUNTIME_PATH_VIEW': ''}, clear=False), patch.object(
                state_paths,
                'require_path_resolver',
                return_value=FakeResolver(),
            ):
                self.assertEqual(state_paths.resolve_control_plane_state_root(), expected)

    def test_control_plane_state_root_preserves_scheduler_container_view(self) -> None:
        expected = Path('/home/openclaw/.openclaw').resolve()
        with patch.dict(os.environ, {'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler'}, clear=False), patch.object(
            state_paths,
            'resolve_state_root',
            return_value=expected,
        ) as resolve_mock:
            self.assertEqual(state_paths.resolve_control_plane_state_root(), expected)

        self.assertEqual(resolve_mock.call_args.kwargs['view'], 'scheduler')

    def test_summary_builders_use_control_plane_state_root_helper(self) -> None:
        expected = Path('/control-plane-state').resolve()
        with patch.object(summary_builders, 'resolve_control_plane_state_root', return_value=expected):
            self.assertEqual(summary_builders._state_root(), expected)

    def test_object_family_control_plane_file_uses_scheduler_state_view(self) -> None:
        expected_root = Path('/home/openclaw/.openclaw').resolve()
        entry = {
            'path_kind': 'host_control_plane_file',
            'path_ref': 'release/evidence/control-plane-run-ledger.json',
        }

        with patch.dict(os.environ, {'OPENCLAW_RUNTIME_PATH_VIEW': 'scheduler'}, clear=False), patch.object(
            object_families,
            'resolve_control_plane_state_root',
            return_value=expected_root,
        ):
            resolved = object_families.resolve_entry_path(entry)

        self.assertEqual(
            Path(resolved),
            expected_root / 'release/evidence/control-plane-run-ledger.json',
        )

    def test_all_object_families_reuses_loaded_contract(self) -> None:
        payload = {
            'families': [
                {
                    'id': 'family_a',
                    'label': 'Family A',
                    'entries': [{'id': 'entry_a', 'path_kind': 'repo_relative', 'path_ref': 'agent/README.md'}],
                },
                {
                    'id': 'family_b',
                    'label': 'Family B',
                    'extensionId': 'agent_probe',
                    'entries': [{'id': 'entry_b', 'path_kind': 'repo_relative', 'path_ref': 'scripts/README.md'}],
                },
            ]
        }
        root_dir = object_families.ROOT_DIR
        with patch.object(object_families, 'load_contract', return_value=payload) as load_contract:
            rows = object_families.all_families(root_dir, config_path=root_dir / 'config/control_plane/service.json')

        self.assertEqual(load_contract.call_count, 1)
        self.assertEqual([row['id'] for row in rows], ['family_a', 'family_b'])
        self.assertEqual(rows[1].get('extensionId'), 'agent_probe')
        self.assertEqual(rows[0]['entries'][0]['resolved_path'], 'agent/README.md')

    def test_exported_group_evidence_presence_uses_group_specific_rows(self) -> None:
        payloads = {
            'control_plane_agent_group_access': {
                'items': [{'groupRef': 'alpha'}],
            },
            'control_plane_agent_access_log': {
                'items': [{'agentGroupRefs': ['alpha', 'beta']}],
            },
            'control_plane_run_ledger': {
                'items': [{'jobId': 'job-a'}],
            },
            'control_plane_agent_group_release_gates': {
                'items': [{'groupRef': 'alpha'}],
            },
            'control_plane_agent_group_acceptance_bindings': {
                'items': [{'groupRef': 'alpha'}],
            },
        }

        with (
            patch.object(agent_group_release, 'evidence_file', side_effect=lambda entry_id: Path(f'/tmp/{entry_id}.json')),
            patch.object(
                agent_group_release,
                'read_json',
                side_effect=lambda path, default=None: payloads.get(Path(path).stem, default),
            ),
        ):
            result = agent_group_release.exported_group_evidence_presence('alpha')

        self.assertEqual(
            result,
            {
                'groupAccessView': True,
                'groupAccessLog': True,
                'runLedger': True,
                'groupReleaseGates': True,
                'acceptanceBindings': True,
            },
        )

    def test_deployment_acceptance_status_reads_object_family_path(self) -> None:
        deployment_path = Path('/state-root/setup/deployment_acceptance.json').resolve()

        with (
            patch.object(
                agent_group_release,
                'object_file',
                return_value=deployment_path,
            ),
            patch.object(
                agent_group_release,
                'read_json',
                side_effect=lambda path, default=None: {
                    'required_checks': [
                        {'id': 'control_plane_registry', 'status': 'PASS'},
                    ]
                }
                if path.as_posix() == deployment_path.as_posix()
                else default,
            ),
        ):
            statuses = agent_group_release.deployment_acceptance_required_check_statuses()

        self.assertEqual(statuses, {'control_plane_registry': 'PASS'})

    def test_group_run_ledger_status_requires_artifact_and_execution(self) -> None:
        run_ledger_summary = {
            'items': [
                {
                    'id': 'artifact_gap_job',
                    'accepted': False,
                    'artifactAccepted': False,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                    'lastFinishedAt': '2026-05-08T20:00:00Z',
                },
                {
                    'id': 'recovered_job',
                    'accepted': False,
                    'artifactAccepted': False,
                    'executionAccepted': False,
                    'effectiveExecutionAccepted': True,
                    'lastFinishedAt': '2026-05-08T20:01:00Z',
                },
            ],
        }

        status = agent_group_release.group_run_ledger_status(['artifact_gap_job', 'recovered_job'], run_ledger_summary)
        rows = agent_group_release.group_required_run_ledger_job_rows(['artifact_gap_job'], run_ledger_summary)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['artifactFailingJobIds'], ['artifact_gap_job', 'recovered_job'])
        self.assertEqual(status['recoveredJobIds'], ['recovered_job'])
        self.assertEqual(rows[0]['effectiveExecutionAccepted'], True)
        self.assertEqual(rows[0]['artifactAccepted'], False)
        self.assertEqual(rows[0]['artifactEffectiveAccepted'], False)
        self.assertEqual(rows[0]['executionAccepted'], True)
        self.assertEqual(rows[0]['lastCompletedAt'], '2026-05-08T20:00:00Z')

    def test_group_run_ledger_status_blocks_missing_artifact(self) -> None:
        run_ledger_summary = {
            'items': [
                {
                    'id': 'artifact_pending_job',
                    'artifactAccepted': None,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                },
            ],
        }

        status = agent_group_release.group_run_ledger_status(['artifact_pending_job'], run_ledger_summary)
        rows = agent_group_release.group_required_run_ledger_job_rows(['artifact_pending_job'], run_ledger_summary)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['artifactPendingJobIds'], ['artifact_pending_job'])
        self.assertIsNone(rows[0]['artifactEffectiveAccepted'])
        self.assertTrue(rows[0]['effectiveExecutionAccepted'])

    def test_group_run_ledger_status_requires_explicit_artifact_evidence(self) -> None:
        run_ledger_summary = {
            'items': [
                {
                    'id': 'legacy_accepted_job',
                    'accepted': True,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                },
            ],
        }

        status = agent_group_release.group_run_ledger_status(['legacy_accepted_job'], run_ledger_summary)
        rows = agent_group_release.group_required_run_ledger_job_rows(['legacy_accepted_job'], run_ledger_summary)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['artifactPendingJobIds'], ['legacy_accepted_job'])
        self.assertIsNone(rows[0]['artifactAccepted'])
        self.assertIsNone(rows[0]['artifactEffectiveAccepted'])
        self.assertTrue(rows[0]['effectiveExecutionAccepted'])

    def test_agent_group_summary_uses_resolved_members_for_health(self) -> None:
        group = {
            'id': 'sample_pipeline',
            'title': 'Sample pipeline',
            'resolvedMembers': ['sample_fetcher', 'sample_digest'],
            'resolvedEntryAgentRefs': ['sample_fetcher'],
            'resolvedExitAgentRefs': ['sample_digest'],
            'schedulePolicy': {'jobRefs': ['sample_fetch_weekday', 'sample_digest_weekday']},
            'dependencyPolicy': {'haltOnMemberFailure': True},
        }
        registry = {
            'agentGroups': [group],
            'agentsById': {
                'sample_fetcher': {'governance': {'moduleRef': 'sample_fetcher'}},
                'sample_digest': {'governance': {'moduleRef': 'sample_digest'}},
            },
        }
        jobs = [
            {
                'id': 'sample_fetch_weekday',
                'agentRef': 'sample_fetcher',
                'enabled': True,
                'state': {'currentStatus': 'succeeded'},
            },
            {
                'id': 'sample_digest_weekday',
                'agentRef': 'sample_digest',
                'enabled': True,
                'state': {'currentStatus': 'succeeded'},
            },
        ]

        [item] = summary_builders._render_agent_group_items(registry, jobs)

        self.assertEqual(item['memberAgentRefs'], ['sample_fetcher', 'sample_digest'])
        self.assertEqual(item['entryAgentRefs'], ['sample_fetcher'])
        self.assertEqual(item['exitAgentRefs'], ['sample_digest'])
        self.assertEqual(item['health']['status'], 'healthy')
        self.assertEqual(item['health']['configuredJobCount'], 2)

    def test_group_access_uses_resolved_members_for_waterfall(self) -> None:
        registry = {
            'agentGroups': [
                {
                    'id': 'sample_pipeline',
                    'title': 'Sample pipeline',
                    'resolvedMembers': ['sample_fetcher', 'sample_digest'],
                }
            ]
        }
        rows = [
            {
                'agentRef': 'sample_fetcher',
                'agentGroupRefs': ['sample_pipeline'],
                'status': 'succeeded',
                'recordedAt': '2026-04-29T00:00:02Z',
            },
            {
                'agentRef': 'sample_digest',
                'agentGroupRefs': ['sample_pipeline'],
                'status': 'succeeded',
                'recordedAt': '2026-04-29T00:00:01Z',
            },
        ]

        with patch.object(access, 'read_agent_access_rows', return_value=rows):
            [item] = access.build_agent_group_access_items(
                registry,
                state_payload_builder=lambda _: {'files': object()},
            )

        self.assertEqual(item['memberAgentRefs'], ['sample_fetcher', 'sample_digest'])
        self.assertEqual([row['agentRef'] for row in item['waterfall']], ['sample_fetcher', 'sample_digest'])

    def test_run_ledger_uses_newer_agent_access_as_effective_status(self) -> None:
        ledger = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'runtimeJobKey': 'sample_ext:sample_digest_weekday',
                    'qualifiedId': 'sample_ext:sample_digest_weekday',
                    'enabled': True,
                    'accepted': False,
                    'executionAccepted': False,
                    'lastFinishedAt': '2026-04-29T00:10:00Z',
                    'latestRun': {
                        'agentRef': 'sample_digest',
                        'groupRef': 'sample_pipeline',
                        'command': ['python', '-m', 'openclaw.cli', '--', 'publish', '--limit', '12'],
                    },
                }
            ],
        }
        access_log = {
            'items': [
                {
                    'agentRef': 'sample_ext:sample_digest',
                    'agentGroupRefs': ['sample_ext:sample_pipeline', 'sample_pipeline'],
                    'runtimeArgs': ['publish', '--limit', '12'],
                    'status': 'succeeded',
                    'source': 'manual_cli',
                    'finishedAt': '2026-04-29T01:00:00Z',
                    'recordedAt': '2026-04-29T01:00:01Z',
                }
            ],
        }

        annotated = run_ledger.apply_latest_agent_access_overlay(ledger, access_log)
        [row] = annotated['items']

        self.assertFalse(row['accepted'])
        self.assertTrue(row['effectiveExecutionAccepted'])
        self.assertEqual(row['effectiveStatus'], 'recovered')
        self.assertEqual(row['latestEffectiveAccess']['source'], 'manual_cli')
        self.assertEqual(annotated['executionEffectiveCounts']['recoveredJobs'], 1)

    def test_run_ledger_newer_matching_failure_overrides_raw_success(self) -> None:
        ledger = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'runtimeJobKey': 'sample_ext:sample_digest_weekday',
                    'qualifiedId': 'sample_ext:sample_digest_weekday',
                    'enabled': True,
                    'accepted': True,
                    'executionAccepted': True,
                    'lastFinishedAt': '2026-04-29T00:10:00Z',
                    'latestRun': {
                        'agentRef': 'sample_digest',
                        'groupRef': 'sample_pipeline',
                        'command': ['python', '-m', 'openclaw.cli', '--', 'publish'],
                    },
                }
            ],
        }
        access_log = {
            'items': [
                {
                    'agentRef': 'sample_ext:sample_digest',
                    'agentGroupRefs': ['sample_pipeline'],
                    'runtimeArgs': ['publish'],
                    'status': 'failed',
                    'finishedAt': '2026-04-29T01:00:00Z',
                }
            ],
        }

        annotated = run_ledger.apply_latest_agent_access_overlay(ledger, access_log)
        [row] = annotated['items']

        self.assertFalse(row['effectiveExecutionAccepted'])
        self.assertEqual(row['effectiveStatus'], 'failed')
        self.assertEqual(annotated['executionEffectiveCounts']['failedJobs'], 1)

    def test_run_ledger_access_status_matching_is_case_insensitive(self) -> None:
        ledger = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'runtimeJobKey': 'sample_ext:sample_digest_weekday',
                    'qualifiedId': 'sample_ext:sample_digest_weekday',
                    'enabled': True,
                    'accepted': False,
                    'executionAccepted': False,
                    'lastFinishedAt': '2026-04-29T00:10:00Z',
                    'latestRun': {
                        'agentRef': 'sample_digest',
                        'groupRef': 'sample_pipeline',
                        'command': ['python', '-m', 'openclaw.cli', '--', 'publish'],
                    },
                }
            ],
        }
        access_log = {
            'items': [
                {
                    'agentRef': 'sample_ext:sample_digest',
                    'agentGroupRefs': ['sample_pipeline'],
                    'runtimeArgs': ['publish'],
                    'status': 'SUCCEEDED',
                    'finishedAt': '2026-04-29T01:00:00Z',
                }
            ],
        }

        annotated = run_ledger.apply_latest_agent_access_overlay(ledger, access_log)
        [row] = annotated['items']

        self.assertTrue(row['effectiveExecutionAccepted'])
        self.assertEqual(row['effectiveStatus'], 'recovered')
        self.assertEqual(annotated['executionEffectiveCounts']['recoveredJobs'], 1)

    def test_run_ledger_execution_effective_counts_keep_artifact_gap_visible(self) -> None:
        ledger = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'runtimeJobKey': 'sample_ext:sample_digest_weekday',
                    'qualifiedId': 'sample_ext:sample_digest_weekday',
                    'enabled': True,
                    'accepted': False,
                    'artifactAccepted': False,
                    'executionAccepted': True,
                }
            ],
        }

        annotated = run_ledger.apply_latest_agent_access_overlay(ledger, {'items': []})
        [row] = annotated['items']

        self.assertTrue(row['effectiveExecutionAccepted'])
        self.assertFalse(row['artifactEffectiveAccepted'])
        self.assertEqual(annotated['executionEffectiveCounts']['acceptedJobs'], 1)
        self.assertEqual(annotated['artifactEffectiveCounts']['failedJobs'], 1)

    def test_run_ledger_ignores_newer_access_with_different_runtime_args(self) -> None:
        ledger = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'runtimeJobKey': 'sample_ext:sample_digest_weekday',
                    'qualifiedId': 'sample_ext:sample_digest_weekday',
                    'enabled': True,
                    'accepted': False,
                    'executionAccepted': False,
                    'lastFinishedAt': '2026-04-29T00:10:00Z',
                    'latestRun': {
                        'agentRef': 'sample_digest',
                        'groupRef': 'sample_pipeline',
                        'command': ['python', '-m', 'openclaw.cli', '--', 'publish', '--limit', '12'],
                    },
                }
            ],
        }
        access_log = {
            'items': [
                {
                    'agentRef': 'sample_ext:sample_digest',
                    'agentGroupRefs': ['sample_pipeline'],
                    'runtimeArgs': ['publish', '--limit', '6'],
                    'status': 'succeeded',
                    'finishedAt': '2026-04-29T01:00:00Z',
                }
            ],
        }

        annotated = run_ledger.apply_latest_agent_access_overlay(ledger, access_log)
        [row] = annotated['items']

        self.assertFalse(row['effectiveExecutionAccepted'])
        self.assertEqual(row['effectiveStatus'], 'failed')
        self.assertNotIn('latestEffectiveAccess', row)

    def test_runtime_acceptance_status_requires_artifact_and_effective_execution(self) -> None:
        run_ledger_payload = {
            'items': [
                {'id': 'sample_fetch_weekday', 'artifactAccepted': True, 'executionAccepted': True, 'effectiveExecutionAccepted': True},
                {'id': 'sample_digest_weekday', 'artifactAccepted': False, 'executionAccepted': False, 'effectiveExecutionAccepted': True},
            ],
        }

        with patch.object(acceptance_summary, 'read_manifest', return_value={'required_run_ledger_jobs': ['sample_fetch_weekday', 'sample_digest_weekday']}):
            status = acceptance_summary.required_run_ledger_status(run_ledger_payload)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['failingJobs'], [])
        self.assertEqual(status['artifactFailingJobs'], ['sample_digest_weekday'])
        self.assertEqual(status['recoveredJobs'], ['sample_digest_weekday'])

    def test_runtime_acceptance_status_blocks_artifact_gap_for_required_jobs(self) -> None:
        run_ledger_payload = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'accepted': False,
                    'artifactAccepted': False,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                },
            ],
        }

        with patch.object(acceptance_summary, 'read_manifest', return_value={'required_run_ledger_jobs': ['sample_digest_weekday']}):
            status = acceptance_summary.required_run_ledger_status(run_ledger_payload)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['failingJobs'], [])
        self.assertEqual(status['artifactFailingJobs'], ['sample_digest_weekday'])

    def test_runtime_acceptance_status_blocks_missing_artifact_for_required_jobs(self) -> None:
        run_ledger_payload = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'artifactAccepted': None,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                },
            ],
        }

        with patch.object(acceptance_summary, 'read_manifest', return_value={'required_run_ledger_jobs': ['sample_digest_weekday']}):
            status = acceptance_summary.required_run_ledger_status(run_ledger_payload)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['failingJobs'], [])
        self.assertEqual(status['artifactMissingJobs'], ['sample_digest_weekday'])
        self.assertEqual(status['artifactFailingJobs'], [])

    def test_runtime_acceptance_status_requires_explicit_artifact_evidence(self) -> None:
        run_ledger_payload = {
            'items': [
                {
                    'id': 'sample_digest_weekday',
                    'accepted': True,
                    'executionAccepted': True,
                    'effectiveExecutionAccepted': True,
                },
            ],
        }

        with patch.object(acceptance_summary, 'read_manifest', return_value={'required_run_ledger_jobs': ['sample_digest_weekday']}):
            status = acceptance_summary.required_run_ledger_status(run_ledger_payload)

        self.assertFalse(status['accepted'])
        self.assertEqual(status['failingJobs'], [])
        self.assertEqual(status['artifactMissingJobs'], ['sample_digest_weekday'])
        self.assertEqual(status['artifactFailingJobs'], [])


if __name__ == '__main__':
    unittest.main()
