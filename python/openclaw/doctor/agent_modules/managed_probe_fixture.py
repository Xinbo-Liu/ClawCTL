#!/usr/bin/env python3
"""Shared managed probe extension fixture for tests and doctor regressions."""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openclaw.lib.repo.path_contracts import extension_anchored_path
from openclaw.doctor.agent_modules.managed_probe_fixture_repo_markers import (
    ensure_repo_markers,
    read_json as _read_json,
    write_json as _write_json,
    write_text as _write_text,
)
from openclaw.doctor.agent_modules.managed_probe_fixture_scaffold import (
    PROBE_CHANGE_CONTROL_DOC_PATHS,
    PROBE_CHECK_ID,
    PROBE_DIAGNOSTIC_ACTION,
    PROBE_EXTENSION_ID,
    PROBE_GROUP_REF,
    PROBE_JOB_REF,
    PROBE_MODEL_REF,
    PROBE_OWNER_DOMAIN,
    PROBE_PACKAGE_NAME,
    PROBE_PRIMARY_MODULE_REF,
    PROBE_RELEASE_CHECK_ID,
    PROBE_RUNTIME_ENTRY_ID,
    PROBE_SUPPORT_MODULE_REF,
    PROBE_TARGET_REF,
    PROBE_TEST_GROUP_ID,
    module_main,
    update_index as _update_index,
    write_control_plane_manifests,
    write_module_fixture,
)

__all__ = (
    'ManagedProbeExtensionFixture',
    'PROBE_CHANGE_CONTROL_DOC_PATHS',
    'PROBE_CHECK_ID',
    'PROBE_DIAGNOSTIC_ACTION',
    'PROBE_EXTENSION_ID',
    'PROBE_GROUP_REF',
    'PROBE_JOB_REF',
    'PROBE_MODEL_REF',
    'PROBE_OWNER_DOMAIN',
    'PROBE_PACKAGE_NAME',
    'PROBE_PRIMARY_MODULE_REF',
    'PROBE_RELEASE_CHECK_ID',
    'PROBE_RUNTIME_ENTRY_ID',
    'PROBE_SUPPORT_MODULE_REF',
    'PROBE_TARGET_REF',
    'PROBE_TEST_GROUP_ID',
    'materialize_managed_probe_extension',
    'remove_managed_extension',
)

_SNAPSHOT_CACHE: dict[tuple[str, str], tuple[tempfile.TemporaryDirectory[str], 'ManagedProbeExtensionFixture']] = {}

BYTECODE_GUARD_INIT = '\n'.join([
    'from __future__ import annotations',
    '',
    'import atexit',
    'import os',
    'import shutil',
    'import sys',
    'from pathlib import Path',
    '',
    "os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')",
    'sys.dont_write_bytecode = True',
    '',
    '',
    'def _cleanup_bytecode_cache() -> None:',
    "    for path in sorted(Path(__file__).resolve().parent.rglob('__pycache__'), reverse=True):",
    '        shutil.rmtree(path, ignore_errors=True)',
    '',
    '',
    '_cleanup_bytecode_cache()',
    'atexit.register(_cleanup_bytecode_cache)',
    '',
])


@dataclass(frozen=True)
class ManagedProbeExtensionFixture:
    repo_root: Path
    base_repo_root: Path
    extension_id: str
    package_root: Path
    service_path: Path
    manifest_dir: Path
    manifest_path: Path
    runtime_paths_path: Path
    testing_manifest_path: Path
    diagnostic_surface_path: Path
    modules_dir: Path
    groups_dir: Path
    jobs_dir: Path
    models_dir: Path
    targets_dir: Path
    python_root: Path
    python_package_dir: Path
    primary_module_dir: Path
    primary_module_path: Path
    primary_main_path: Path
    support_module_dir: Path
    support_module_path: Path
    support_main_path: Path
    shared_runtime_layout_path: Path


def _fixture_layout(repo_root: Path, *, base_repo_root: Path, extension_id: str) -> ManagedProbeExtensionFixture:
    package_root = repo_root / 'agent' / 'extensions' / extension_id
    manifest_dir = package_root / 'config' / 'control_plane' / 'extensions.d'
    profiles_dir = package_root / 'config' / 'control_plane' / 'profiles'
    modules_dir = package_root / 'agent' / 'modules'
    groups_dir = package_root / 'agent' / 'control_plane' / 'groups'
    jobs_dir = package_root / 'agent' / 'control_plane' / 'jobs'
    models_dir = package_root / 'agent' / 'control_plane' / 'models'
    targets_dir = package_root / 'agent' / 'control_plane' / 'targets'
    python_root = package_root / 'python'
    python_package_dir = python_root / PROBE_PACKAGE_NAME
    shared_dir = python_package_dir / 'domains' / PROBE_OWNER_DOMAIN / 'shared'
    primary_module_dir = modules_dir / PROBE_PRIMARY_MODULE_REF
    support_module_dir = modules_dir / PROBE_SUPPORT_MODULE_REF
    service_path = profiles_dir / f'{extension_id}.service.json'
    manifest_path = manifest_dir / f'{extension_id}.json'
    runtime_paths_path = manifest_dir / f'{extension_id}.runtime_paths.json'
    testing_manifest_path = manifest_dir / f'{extension_id}.testing_manifest.json'
    diagnostic_surface_path = manifest_dir / f'{extension_id}.diagnostic_surface.json'
    return ManagedProbeExtensionFixture(
        repo_root=repo_root.resolve(),
        base_repo_root=base_repo_root.resolve(),
        extension_id=extension_id,
        package_root=package_root.resolve(),
        service_path=service_path.resolve(),
        manifest_dir=manifest_dir.resolve(),
        manifest_path=manifest_path.resolve(),
        runtime_paths_path=runtime_paths_path.resolve(),
        testing_manifest_path=testing_manifest_path.resolve(),
        diagnostic_surface_path=diagnostic_surface_path.resolve(),
        modules_dir=modules_dir.resolve(),
        groups_dir=groups_dir.resolve(),
        jobs_dir=jobs_dir.resolve(),
        models_dir=models_dir.resolve(),
        targets_dir=targets_dir.resolve(),
        python_root=python_root.resolve(),
        python_package_dir=python_package_dir.resolve(),
        primary_module_dir=primary_module_dir.resolve(),
        primary_module_path=(primary_module_dir / 'module.json').resolve(),
        primary_main_path=(python_package_dir / 'modules' / PROBE_PRIMARY_MODULE_REF / 'main.py').resolve(),
        support_module_dir=support_module_dir.resolve(),
        support_module_path=(support_module_dir / 'module.json').resolve(),
        support_main_path=(python_package_dir / 'modules' / PROBE_SUPPORT_MODULE_REF / 'main.py').resolve(),
        shared_runtime_layout_path=(shared_dir / 'runtime_layout.py').resolve(),
    )


def _repo_root_is_empty(repo_root: Path) -> bool:
    return not any(repo_root.iterdir())


def _managed_probe_snapshot(
    *,
    base_repo_root: Path,
    extension_id: str,
) -> ManagedProbeExtensionFixture:
    cache_key = (base_repo_root.as_posix(), extension_id)
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached is None:
        snapshot_dir = tempfile.TemporaryDirectory(prefix=f'managed-probe-snapshot-{extension_id}-')
        snapshot_repo_root = Path(snapshot_dir.name).resolve() / 'repo'
        snapshot_repo_root.mkdir(parents=True, exist_ok=True)
        snapshot_fixture = _materialize_fixture(
            _fixture_layout(snapshot_repo_root, base_repo_root=base_repo_root, extension_id=extension_id)
        )
        cached = (snapshot_dir, snapshot_fixture)
        _SNAPSHOT_CACHE[cache_key] = cached
    return cached[1]


def _probe_dispatch_registry_payload() -> dict[str, object]:
    return {
        'version': 7,
        'defaults': {
            'dedupeWindowHours': 36,
            'maxAttempts': 5,
            'backoffSeconds': [60, 300, 900],
            'targetMinIntervalMs': 250,
            'targetMaxPerSecond': 4,
            'targetMaxPerMinute': 80,
            'targetRateLimitStateTtlSeconds': 600,
        },
        'targets': [
            {
                'id': PROBE_TARGET_REF,
                'targetGroup': 'test',
                'deliveryTier': 'validation',
                'messageProfile': 'test_detail',
                'enabledDefault': False,
                'enabledEnv': 'PROBE_DISPATCH_ENABLE',
                'secretEnv': 'PROBE_DISPATCH_BOT_SECRET',
                'titleEnv': 'PROBE_DISPATCH_TITLE',
                'atAllEnv': 'PROBE_DISPATCH_AT_ALL',
                'formatEnv': 'PROBE_DISPATCH_MSG_FORMAT',
                'silenceEnabledDefault': False,
                'silenceEnabledEnv': 'PROBE_DISPATCH_SILENCE_ENABLE',
                'silenceMinDeltaDefault': 0.2,
                'silenceMinDeltaEnv': 'PROBE_DISPATCH_SILENCE_MIN_DELTA',
                'allowedReleaseLevelsDefault': ['official', 'review', 'degraded'],
                'allowedReleaseLevelsEnv': 'PROBE_DISPATCH_ALLOWED_RELEASE_LEVELS',
                'formatDefault': 'post',
                'secretRequiredDefault': False,
                'endpointIsolationDefault': True,
                'titleDefault': 'Probe Dispatch Target',
                'boundary': {
                    'dispatchLane': 'integration_validation',
                    'payloadScope': 'validation_digest',
                    'publishLatestDefault': False,
                    'description': 'Probe validation target for managed extension regressions.',
                },
                'atAllDefault': False,
                'verificationOrderDefault': 10,
                'owner': {
                    'team': 'Managed Probe',
                    'primary': 'Probe Owner',
                    'backup': 'Probe Backup',
                },
                'releasePolicyId': 'probe_validation_all_levels',
                'verificationBatchIds': ['probe_validation'],
                'lifecycleState': 'disabled',
                'rotationClass': 'validation_flexible',
                'transport': 'webhook',
                'provider': 'synthetic_webhook',
                'endpointEnv': 'PROBE_DISPATCH_WEBHOOK_URL',
            }
        ],
        'releasePolicies': [
            {
                'id': 'probe_validation_all_levels',
                'title': 'Probe Validation Policy',
                'description': 'Managed probe validation target policy.',
                'allowedReleaseLevels': ['official', 'review', 'degraded'],
            }
        ],
        'verificationBatches': {
            'defaultRotationBatchId': 'probe_validation',
            'batches': [
                {
                    'id': 'probe_validation',
                    'title': 'Probe Validation Batch',
                    'description': 'Managed probe validation batch.',
                    'requiredForRelease': False,
                    'targetIds': [PROBE_TARGET_REF],
                    'requiredTargetGroups': ['test'],
                }
            ],
        },
        'lifecycleStates': [
            {
                'id': 'active',
                'title': 'Active',
                'description': 'Target can be enabled.',
                'enableAllowed': True,
                'decommissioned': False,
            },
            {
                'id': 'disabled',
                'title': 'Disabled',
                'description': 'Target is registered but not enabled by default.',
                'enableAllowed': True,
                'decommissioned': False,
            },
            {
                'id': 'decommissioned',
                'title': 'Decommissioned',
                'description': 'Target is retired.',
                'enableAllowed': False,
                'decommissioned': True,
            },
        ],
    }


def remove_managed_extension(repo_root: Path, extension_id: str) -> None:
    repo_root = Path(repo_root).resolve()
    package_root = repo_root / 'agent' / 'extensions' / extension_id
    if package_root.exists():
        shutil.rmtree(package_root, ignore_errors=True)
    index_path = repo_root / 'agent' / 'extensions' / 'index.json'
    if not index_path.exists():
        return
    payload = _read_json(index_path)
    rows = payload.get('extensions')
    if not isinstance(rows, list):
        return
    payload['extensions'] = [
        row
        for row in rows
        if not (isinstance(row, dict) and str(row.get('id') or '').strip() == extension_id)
    ]
    _write_json(index_path, payload)


def _materialize_fixture(fixture: ManagedProbeExtensionFixture) -> ManagedProbeExtensionFixture:
    ensure_repo_markers(fixture.repo_root, fixture.base_repo_root)
    remove_managed_extension(fixture.repo_root, fixture.extension_id)
    _update_index(fixture.repo_root, fixture.extension_id)
    write_control_plane_manifests(
        repo_root=fixture.repo_root,
        extension_id=fixture.extension_id,
        service_path=fixture.service_path,
        manifest_path=fixture.manifest_path,
        runtime_paths_path=fixture.runtime_paths_path,
        testing_manifest_path=fixture.testing_manifest_path,
        diagnostic_surface_path=fixture.diagnostic_surface_path,
    )

    primary_source_paths = [
        extension_anchored_path(f'python/{PROBE_PACKAGE_NAME}/modules/{PROBE_PRIMARY_MODULE_REF}/main.py'),
        extension_anchored_path(f'python/{PROBE_PACKAGE_NAME}/domains/{PROBE_OWNER_DOMAIN}/shared'),
    ]
    support_source_paths = [
        extension_anchored_path(f'python/{PROBE_PACKAGE_NAME}/modules/{PROBE_SUPPORT_MODULE_REF}/main.py'),
        extension_anchored_path(f'python/{PROBE_PACKAGE_NAME}/domains/{PROBE_OWNER_DOMAIN}/shared'),
    ]

    write_module_fixture(
        extension_id=fixture.extension_id,
        module_dir=fixture.primary_module_dir,
        module_ref=PROBE_PRIMARY_MODULE_REF,
        runtime_module=f'{PROBE_PACKAGE_NAME}.modules.{PROBE_PRIMARY_MODULE_REF}.main',
        source_paths=primary_source_paths,
        entrypoint_kind='delivery_adapter',
        external_dispatch=True,
        operations={
            'send_default': {
                'summary': 'Deliver probe payloads through the registered dispatch target.',
                'executor': {
                    'kind': 'delivery_adapter',
                    'operation': 'send',
                },
                'jobBindings': {
                    PROBE_JOB_REF: {
                        'targetBindingRef': PROBE_TARGET_REF,
                    }
                },
            }
        },
        filesystem_write=[PROBE_RUNTIME_ENTRY_ID],
    )
    write_module_fixture(
        extension_id=fixture.extension_id,
        module_dir=fixture.support_module_dir,
        module_ref=PROBE_SUPPORT_MODULE_REF,
        runtime_module=f'{PROBE_PACKAGE_NAME}.modules.{PROBE_SUPPORT_MODULE_REF}.main',
        source_paths=support_source_paths,
        entrypoint_kind='python_cli',
        external_dispatch=False,
        operations={
            'inspect_default': {
                'summary': 'Inspect probe extension shared surfaces.',
                'executor': {
                    'kind': 'python_cli',
                    'argv': ['inspect'],
                },
                'jobBindings': {},
            }
        },
        filesystem_write=[],
    )
    _write_json(
        fixture.groups_dir / f'{PROBE_GROUP_REF}.json',
        {
            'schemaVersion': 1,
            'id': PROBE_GROUP_REF,
            'activation': {
                'enabledExtensionIds': [fixture.extension_id],
            },
            'title': 'Probe Pipeline',
            'mission': 'Exercise one minimal managed extension pipeline.',
            'ownerDomain': PROBE_OWNER_DOMAIN,
            'dependencyPolicy': {
                'haltOnMemberFailure': True,
                'retryMode': 'stage_owned',
                'orderedJobRefs': [PROBE_JOB_REF],
            },
            'artifactContract': {
                'inputs': [],
                'outputs': ['probe_dispatch_report_json'],
                'summaryArtifact': 'probe_pipeline_health_json',
            },
            'schedulePolicy': {
                'timezone': 'Asia/Shanghai',
                'windowRef': 'weekday_probe',
                'orderBase': 10,
                'orderStep': 10,
                'jobRefs': [PROBE_JOB_REF],
            },
            'failurePolicy': {
                'manualInterventionSignals': ['probe_dispatch_failed'],
                'degradePolicy': 'Freeze the pipeline until the probe target is repaired.',
            },
            'observabilityContract': {
                'summaryObject': 'probe_pipeline_health_json',
                'runLedgerRequired': True,
            },
            'releasePolicy': {
                'changeControl': 'group_contract_required',
                'singleSourceDocs': ['docs/architecture/control-plane-baseline.md'],
                'requiredEvidence': ['group_access_view', 'run_ledger'],
                'releaseGate': {
                    'requiredCheckIds': ['group_health', 'run_ledger'],
                    'requireHealthyMembers': True,
                    'requireRecentAccess': False,
                    'requireRunLedgerCoverage': True,
                    'freezeOnStatuses': ['failed', 'blocked'],
                },
                'acceptanceBinding': {
                    'deploymentAcceptanceCheckIds': ['control_plane_registry'],
                    'runtimeEvidenceEntryIds': ['control_plane_run_ledger'],
                    'requiredRunLedgerJobRefs': [PROBE_JOB_REF],
                },
                'rollbackContract': {
                    'strategy': 'replay_from_probe_entry',
                    'triggerSignals': ['probe_dispatch_failed'],
                    'requiredEvidenceRefs': ['run_ledger'],
                    'operatorSteps': ['Replay the managed probe job after fixing the target.'],
                    'maxRecoveryMinutes': 30,
                },
            },
        },
    )
    _write_json(
        fixture.jobs_dir / f'50_{PROBE_JOB_REF}.json',
        {
            'schemaVersion': 1,
            'id': PROBE_JOB_REF,
            'title': 'Probe Dispatch Weekday',
            'activation': {
                'enabledExtensionIds': [fixture.extension_id],
            },
            'agentRef': PROBE_PRIMARY_MODULE_REF,
            'modelProfileRef': PROBE_MODEL_REF,
            'targetBindingRef': PROBE_TARGET_REF,
            'schedule': {
                'kind': 'cron',
                'expr': '0 9 * * 1-5',
                'tz': 'Asia/Shanghai',
            },
            'concurrencyPolicy': 'forbid',
            'timeoutSeconds': 600,
            'retryPolicy': {
                'enabled': False,
                'maxAttempts': 0,
                'backoffSeconds': [],
            },
            'artifactPolicy': {
                'runArtifactRoot': 'control_plane/probe_dispatcher',
                'latestAlias': 'latest',
                'retentionDays': 14,
            },
            'failureClassPolicy': {
                'retryableClasses': [],
                'terminalClasses': ['probe_dispatch_failed'],
            },
            'groupRef': PROBE_GROUP_REF,
            'runnerRef': 'agent_runtime',
            'operationRef': 'send_default',
        },
    )
    _write_json(
        fixture.models_dir / f'{PROBE_MODEL_REF}.json',
        {
            'schemaVersion': 1,
            'id': PROBE_MODEL_REF,
            'activation': {
                'enabledExtensionIds': [fixture.extension_id],
            },
            'title': 'Probe Default Model',
            'provider': 'openai',
            'channel': {
                'kind': 'http',
                'api': 'openai-chat-completions',
                'baseUrlEnv': 'OPENAI_BASE_URL',
                'apiKeyEnv': 'OPENAI_API_KEY',
                'auth': {
                    'kind': 'api_key',
                    'required': True,
                },
            },
            'modelRef': 'openai/gpt-5.4-mini',
            'capabilities': {
                'reasoning': True,
                'toolUse': False,
                'vision': False,
                'contextWindow': 128000,
                'maxTokens': 4096,
            },
            'timeoutPolicy': {
                'requestTimeoutSeconds': 60,
                'jobDefaultTimeoutSeconds': 600,
            },
            'rateLimits': {
                'requestsPerMinute': 20,
                'maxConcurrentRequests': 1,
            },
            'costPolicy': {
                'currency': 'USD',
                'billingMode': 'not_applicable',
                'pricingSource': {
                    'kind': 'fixture_not_applicable',
                    'notes': ['Managed probe model profile is a registry fixture and is not used for live billing.'],
                },
                'tokenRates': {
                    'inputPerMillionTokens': 0,
                    'outputPerMillionTokens': 0,
                },
                'estimation': {
                    'inputCharsPerToken': 4,
                    'outputCharsPerToken': 4,
                },
                'budget': {
                    'enforcement': 'off',
                    'maxEstimatedCostPerCall': 0,
                    'dailySoftLimit': 0,
                    'dailyHardLimit': 0,
                    'maxEstimatedInputTokensPerCall': 0,
                    'maxEstimatedOutputTokensPerCall': 0,
                },
                'riskPolicy': {
                    'allowZeroRates': True,
                    'allowEstimatedUsage': True,
                },
            },
        },
    )
    _write_json(
        fixture.targets_dir / f'{PROBE_TARGET_REF}.json',
        {
            'schemaVersion': 1,
            'id': PROBE_TARGET_REF,
            'activation': {
                'enabledExtensionIds': [fixture.extension_id],
            },
            'title': 'Probe Dispatch Target',
            'adapterKind': 'message_sink',
            'provider': 'synthetic_webhook',
            'transport': 'webhook',
            'notes': ['Managed probe target for zero-arg extension regressions.'],
            'supportedMessageFormats': ['text', 'post'],
            'deliveryContract': {
                'successStatuses': ['sent', 'noop'],
                'retryableStatuses': ['retry_pending'],
                'terminalStatuses': ['failed', 'blocked'],
            },
            'supportedAgentRefs': [PROBE_PRIMARY_MODULE_REF],
            'operations': {
                'preflight': {'argv': ['preflight']},
                'status': {'argv': ['status']},
                'send': {'argv': ['send']},
                'retry': {'argv': ['retry']},
                'explain_latest': {'argv': ['explain-latest']},
            },
        },
    )
    _write_json(
        fixture.package_root / 'agent' / 'control_plane' / 'registries' / 'dispatch_targets.json',
        _probe_dispatch_registry_payload(),
    )
    _write_text(fixture.python_package_dir / '__init__.py', BYTECODE_GUARD_INIT)
    _write_text(fixture.python_package_dir / 'modules' / '__init__.py', '')
    _write_text(fixture.python_package_dir / 'modules' / PROBE_PRIMARY_MODULE_REF / '__init__.py', '')
    _write_text(fixture.primary_main_path, module_main(PROBE_PRIMARY_MODULE_REF, 'probe_dispatcher'))
    _write_text(fixture.python_package_dir / 'modules' / PROBE_SUPPORT_MODULE_REF / '__init__.py', '')
    _write_text(fixture.support_main_path, module_main(PROBE_SUPPORT_MODULE_REF, 'probe_helper'))
    _write_text(fixture.python_package_dir / 'domains' / '__init__.py', '')
    _write_text(fixture.python_package_dir / 'domains' / PROBE_OWNER_DOMAIN / '__init__.py', '')
    _write_text(fixture.python_package_dir / 'domains' / PROBE_OWNER_DOMAIN / 'shared' / '__init__.py', '')
    _write_text(
        fixture.shared_runtime_layout_path,
        '\n'.join([
            'from __future__ import annotations',
            '',
            "PROBE_RUNTIME_ENTRY_ID = 'probe_dispatch_out_dir'",
            '',
        ]),
    )
    return fixture


def materialize_managed_probe_extension(
    repo_root: Path,
    *,
    base_repo_root: Path | None = None,
    extension_id: str = PROBE_EXTENSION_ID,
    use_snapshot_cache: bool = True,
) -> ManagedProbeExtensionFixture:
    repo_root = Path(repo_root).resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    base_repo_root = repo_root if base_repo_root is None else Path(base_repo_root).resolve()
    fixture = _fixture_layout(repo_root, base_repo_root=base_repo_root, extension_id=extension_id)
    if use_snapshot_cache:
        cached_fixture = _managed_probe_snapshot(base_repo_root=base_repo_root, extension_id=extension_id)
        if _repo_root_is_empty(repo_root):
            shutil.copytree(
                cached_fixture.repo_root,
                repo_root,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'),
            )
            return fixture
        ensure_repo_markers(repo_root, base_repo_root)
        remove_managed_extension(repo_root, extension_id)
        shutil.copytree(
            cached_fixture.package_root,
            fixture.package_root,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'),
        )
        _update_index(repo_root, extension_id)
        return fixture
    return _materialize_fixture(fixture)
