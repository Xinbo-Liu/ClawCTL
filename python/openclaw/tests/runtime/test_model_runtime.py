from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from openclaw.lib.http.json_client import JsonHttpResult
from openclaw.lib.models.client import ModelClientError, generate_text
from openclaw.lib.models.cost_policy import ModelCostPolicyError, validate_model_cost_policy
from openclaw.lib.models.env import model_env_specs_from_registry
from openclaw.lib.models.registry import ModelProfile, ModelRegistryError, load_model_profile


TEST_OLLAMA_BASE_URL = 'http://ollama-provider.invalid:11434'
TEST_OLLAMA_MODEL_REF = 'ollama/__TEST_MODEL__'
TEST_MINIMAX_MODEL_REF = 'minimax/__TEST_MODEL__'
TEST_LOCAL_MODEL_REF = 'local/__TEST_MODEL__'


def _cost_policy(**overrides):
    payload = {
        'currency': 'USD',
        'billingMode': 'pay_as_you_go',
        'pricingSource': {
            'kind': 'test_declared',
            'url': 'https://example.test/model-pricing',
            'checkedAt': '2026-04-27',
        },
        'tokenRates': {
            'inputPerMillionTokens': 0.3,
            'outputPerMillionTokens': 1.2,
        },
        'estimation': {
            'inputCharsPerToken': 4,
            'outputCharsPerToken': 4,
        },
        'budget': {
            'enforcement': 'hard',
            'maxEstimatedCostPerCall': 0.05,
            'dailySoftLimit': 0.25,
            'dailyHardLimit': 1.0,
            'maxEstimatedInputTokensPerCall': 100000,
            'maxEstimatedOutputTokensPerCall': 8192,
        },
        'riskPolicy': {
            'allowZeroRates': False,
            'allowEstimatedUsage': True,
        },
    }
    payload.update(overrides)
    return payload


def _profile(**overrides) -> ModelProfile:
    payload = {
        'profile_id': 'test_model',
        'provider': 'ollama',
        'model_ref': TEST_OLLAMA_MODEL_REF,
        'model_ref_env': '',
        'base_url_env': 'OLLAMA_BASE_URL',
        'api_key_env': '',
        'channel': {'kind': 'http', 'api': 'ollama-chat', 'baseUrlEnv': 'OLLAMA_BASE_URL', 'auth': {'kind': 'none', 'required': False}},
        'request_timeout_seconds': 5,
        'job_default_timeout_seconds': 30,
        'capabilities': {'maxTokens': 1024},
        'rate_limits': {'requestsPerMinute': 100, 'maxConcurrentRequests': 1},
        'cost_policy': _cost_policy(),
        'raw': {},
    }
    payload.update(overrides)
    return ModelProfile(**payload)


class ModelRuntimeTest(unittest.TestCase):
    def test_ollama_http_channel_uses_api_chat(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'OPENCLAW_MODEL_RUNTIME_STATE_DIR': tmp,
            'OLLAMA_BASE_URL': TEST_OLLAMA_BASE_URL,
        }, clear=False):
            captured: dict[str, object] = {}

            def fake_post(url, payload, *, timeout_sec, headers):
                captured.update({'url': url, 'payload': payload, 'headers': headers, 'timeout': timeout_sec})
                return JsonHttpResult(
                    ok=True,
                    status_code=200,
                    payload={'message': {'content': '本地模型输出'}, 'prompt_eval_count': 4, 'eval_count': 6},
                    raw_text='{}',
                    error=None,
                )

            with mock.patch('openclaw.lib.models.client.load_model_profile', return_value=_profile()), mock.patch('openclaw.lib.models.client.http_post_json', side_effect=fake_post):
                result = generate_text(model_profile_ref='test_model', prompt='hello', system_prompt='sys', max_tokens=64)

        self.assertEqual(result.text, '本地模型输出')
        self.assertEqual(captured['url'], f'{TEST_OLLAMA_BASE_URL}/api/chat')
        self.assertEqual((captured['payload'] or {}).get('stream'), False)
        self.assertEqual((captured['payload'] or {}).get('think'), False)
        self.assertEqual((captured['payload'] or {}).get('options'), {'num_predict': 64})
        self.assertEqual((result.actual_cost or {}).get('basis'), 'provider_usage')

    def test_http_channel_requires_declared_api_key(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'OPENCLAW_MODEL_RUNTIME_STATE_DIR': tmp,
            'MINIMAX_BASE_URL': 'https://api.example.test',
        }, clear=False):
            os.environ.pop('MINIMAX_API_KEY', None)
            profile = _profile(
                provider='minimax',
                model_ref=TEST_MINIMAX_MODEL_REF,
                base_url_env='MINIMAX_BASE_URL',
                api_key_env='MINIMAX_API_KEY',
                channel={'kind': 'http', 'api': 'anthropic-messages', 'baseUrlEnv': 'MINIMAX_BASE_URL', 'apiKeyEnv': 'MINIMAX_API_KEY', 'auth': {'kind': 'api_key', 'required': True}},
            )
            with mock.patch('openclaw.lib.models.client.load_model_profile', return_value=profile):
                with self.assertRaises(ModelClientError):
                    generate_text(model_profile_ref='test_model', prompt='hello')

    def test_local_process_channel_reads_json_stdout(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            script = base / 'local_model.py'
            script.write_text(
                "import json, sys\n"
                "payload=json.loads(sys.stdin.read())\n"
                "print(json.dumps({'text': 'local:' + payload['model']}))\n",
                encoding='utf-8',
            )
            profile = _profile(
                provider='local_llm',
                model_ref=TEST_LOCAL_MODEL_REF,
                base_url_env='',
                api_key_env='',
                channel={'kind': 'local_process', 'api': 'local-process-json', 'localProcess': {'command': [sys.executable, str(script)]}},
            )
            with mock.patch.dict(os.environ, {'OPENCLAW_MODEL_RUNTIME_STATE_DIR': str(base / 'state')}, clear=False), mock.patch('openclaw.lib.models.client.load_model_profile', return_value=profile):
                result = generate_text(model_profile_ref='test_model', prompt='hello')
        self.assertEqual(result.text, f'local:{TEST_LOCAL_MODEL_REF}')

    def test_audit_log_hashes_error_text_without_plaintext(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'OPENCLAW_MODEL_RUNTIME_STATE_DIR': tmp,
            'OLLAMA_BASE_URL': TEST_OLLAMA_BASE_URL,
        }, clear=False):
            profile = _profile()
            with mock.patch('openclaw.lib.models.client.load_model_profile', return_value=profile), mock.patch(
                'openclaw.lib.models.client.http_post_json',
                return_value=JsonHttpResult(ok=False, status_code=500, payload={}, raw_text='', error='leaked prompt text'),
            ):
                with self.assertRaises(ModelClientError):
                    generate_text(model_profile_ref='test_model', prompt='sensitive prompt')
            audit_path = Path(tmp) / 'audit' / 'model_calls.jsonl'
            payload = json.loads(audit_path.read_text(encoding='utf-8').splitlines()[-1])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn('errorSha1', payload)
        self.assertIn('errorChars', payload)
        self.assertIn('costEstimate', payload)
        self.assertNotIn('leaked prompt text', serialized)
        self.assertNotIn('sensitive prompt', serialized)

    def test_cost_policy_rejects_zero_paygo_rates(self) -> None:
        profile = _profile(cost_policy=_cost_policy(tokenRates={'inputPerMillionTokens': 0, 'outputPerMillionTokens': 0}))
        with self.assertRaises(ModelCostPolicyError):
            validate_model_cost_policy(profile)

    def test_cost_budget_blocks_oversized_request_before_http(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'OPENCLAW_MODEL_RUNTIME_STATE_DIR': tmp,
            'OLLAMA_BASE_URL': TEST_OLLAMA_BASE_URL,
        }, clear=False):
            profile = _profile(
                cost_policy=_cost_policy(
                    budget={
                        'enforcement': 'hard',
                        'maxEstimatedCostPerCall': 0.000001,
                        'dailySoftLimit': 0.25,
                        'dailyHardLimit': 1.0,
                        'maxEstimatedInputTokensPerCall': 100000,
                        'maxEstimatedOutputTokensPerCall': 8192,
                    },
                )
            )
            with mock.patch('openclaw.lib.models.client.load_model_profile', return_value=profile), mock.patch(
                'openclaw.lib.models.client.http_post_json'
            ) as post:
                with self.assertRaises(ModelClientError):
                    generate_text(model_profile_ref='test_model', prompt='hello', max_tokens=1024)
            post.assert_not_called()

    def test_cost_budget_audit_mode_does_not_block_request(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'OPENCLAW_MODEL_RUNTIME_STATE_DIR': tmp,
            'OLLAMA_BASE_URL': TEST_OLLAMA_BASE_URL,
        }, clear=False):
            profile = _profile(
                cost_policy=_cost_policy(
                    budget={
                        'enforcement': 'audit',
                        'maxEstimatedCostPerCall': 0.000001,
                        'dailySoftLimit': 0.000001,
                        'dailyHardLimit': 0.000001,
                        'maxEstimatedInputTokensPerCall': 1,
                        'maxEstimatedOutputTokensPerCall': 1,
                    },
                )
            )
            with mock.patch('openclaw.lib.models.client.load_model_profile', return_value=profile), mock.patch(
                'openclaw.lib.models.client.http_post_json',
                return_value=JsonHttpResult(ok=True, status_code=200, payload={'message': {'content': 'ok'}}, raw_text='{}', error=None),
            ) as post:
                result = generate_text(model_profile_ref='test_model', prompt='hello', max_tokens=1024)
            self.assertEqual(result.text, 'ok')
            self.assertTrue(post.called)

    def test_model_env_specs_include_http_and_local_channels(self) -> None:
        registry = {
            'jobs': [{'id': 'j1', 'modelProfileRef': 'ollama_default'}, {'id': 'j2', 'modelProfileRef': 'local_default'}],
            'modelsById': {
                'ollama_default': {
                    'id': 'ollama_default',
                    'modelRefEnv': 'OLLAMA_MODEL_REF',
                    'channel': {'kind': 'http', 'api': 'ollama-chat', 'baseUrlEnv': 'OLLAMA_BASE_URL', 'auth': {'kind': 'none', 'required': False}},
                },
                'local_default': {
                    'id': 'local_default',
                    'channel': {'kind': 'local_process', 'api': 'local-process-json', 'localProcess': {'commandEnv': 'LOCAL_MODEL_COMMAND'}},
                },
            },
        }
        specs = model_env_specs_from_registry(registry)
        self.assertTrue(specs['OLLAMA_MODEL_REF'].required)
        self.assertEqual(specs['OLLAMA_MODEL_REF'].purpose, 'model_ref')
        self.assertTrue(specs['OLLAMA_BASE_URL'].required)
        self.assertFalse(specs['OLLAMA_BASE_URL'].secret)
        self.assertTrue(specs['LOCAL_MODEL_COMMAND'].required)
        self.assertEqual(specs['LOCAL_MODEL_COMMAND'].purpose, 'local_model_command')

    def test_model_registry_errors_surface_as_client_errors(self) -> None:
        with mock.patch('openclaw.lib.models.client.load_model_profile', side_effect=ModelRegistryError('未注册的 modelProfileRef：missing')):
            with self.assertRaisesRegex(ModelClientError, '未注册的 modelProfileRef：missing'):
                generate_text(model_profile_ref='missing', prompt='hello')

    def test_model_profile_path_selection_errors_are_registry_errors(self) -> None:
        with mock.patch('openclaw.lib.models.registry.resolve_control_plane_service_config_path', side_effect=ValueError('control plane profile mismatch')):
            with self.assertRaisesRegex(ModelRegistryError, 'control plane profile mismatch'):
                load_model_profile('ollama_default')

    def test_model_profile_resolves_model_ref_from_env_without_repo_literal(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'OLLAMA_MODEL_REF': '__ENV_MODEL__'}, clear=False):
            models_dir = Path(tmp) / 'models'
            models_dir.mkdir()
            payload = {
                'schemaVersion': 1,
                'id': 'ollama_default',
                'provider': 'ollama',
                'modelRef': 'ollama/__REQUIRED_MODEL__',
                'modelRefEnv': 'OLLAMA_MODEL_REF',
                'channel': {'kind': 'http', 'api': 'ollama-chat', 'baseUrlEnv': 'OLLAMA_BASE_URL', 'auth': {'kind': 'none', 'required': False}},
                'timeoutPolicy': {'requestTimeoutSeconds': 5, 'jobDefaultTimeoutSeconds': 30},
                'capabilities': {'maxTokens': 1024},
                'rateLimits': {'requestsPerMinute': 100, 'maxConcurrentRequests': 1},
                'costPolicy': _cost_policy(
                    billingMode='self_hosted',
                    tokenRates={'inputPerMillionTokens': 0, 'outputPerMillionTokens': 0},
                    budget={
                        'enforcement': 'hard',
                        'maxEstimatedCostPerCall': 0,
                        'dailySoftLimit': 0,
                        'dailyHardLimit': 0,
                        'maxEstimatedInputTokensPerCall': 100000,
                        'maxEstimatedOutputTokensPerCall': 8192,
                    },
                    riskPolicy={'allowZeroRates': True, 'allowEstimatedUsage': True},
                ),
            }
            (models_dir / 'ollama_default.json').write_text(json.dumps(payload), encoding='utf-8')
            with mock.patch('openclaw.lib.models.registry._model_profiles_dirs', return_value=[models_dir]):
                profile = load_model_profile('ollama_default')
        self.assertEqual(profile.model_ref, 'ollama/__ENV_MODEL__')
        self.assertEqual(profile.remote_model_name, '__ENV_MODEL__')

    def test_qualified_model_profile_ref_resolves_duplicate_extension_local_ids(self) -> None:
        def payload(model_ref: str) -> dict[str, object]:
            return {
                'schemaVersion': 1,
                'id': 'ollama_default',
                'provider': 'ollama',
                'modelRef': model_ref,
                'modelRefEnv': '',
                'channel': {'kind': 'http', 'api': 'ollama-chat', 'baseUrlEnv': 'OLLAMA_BASE_URL', 'auth': {'kind': 'none', 'required': False}},
                'timeoutPolicy': {'requestTimeoutSeconds': 5, 'jobDefaultTimeoutSeconds': 30},
                'capabilities': {'maxTokens': 1024},
                'rateLimits': {'requestsPerMinute': 100, 'maxConcurrentRequests': 1},
                'costPolicy': _cost_policy(
                    billingMode='self_hosted',
                    tokenRates={'inputPerMillionTokens': 0, 'outputPerMillionTokens': 0},
                    budget={
                        'enforcement': 'hard',
                        'maxEstimatedCostPerCall': 0,
                        'dailySoftLimit': 0,
                        'dailyHardLimit': 0,
                        'maxEstimatedInputTokensPerCall': 100000,
                        'maxEstimatedOutputTokensPerCall': 8192,
                    },
                    riskPolicy={'allowZeroRates': True, 'allowEstimatedUsage': True},
                ),
            }

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / 'agent' / 'extensions' / 'ext_alpha' / 'agent' / 'control_plane' / 'models'
            second_dir = root / 'agent' / 'extensions' / 'ext_beta' / 'agent' / 'control_plane' / 'models'
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            (first_dir / 'ollama_default.json').write_text(json.dumps(payload('ollama/alpha')), encoding='utf-8')
            (second_dir / 'ollama_default.json').write_text(json.dumps(payload('ollama/beta')), encoding='utf-8')
            with mock.patch('openclaw.lib.models.registry._model_profiles_dirs', return_value=[first_dir, second_dir]):
                with self.assertRaisesRegex(ModelRegistryError, '多个目录重复注册'):
                    load_model_profile('ollama_default')
                profile = load_model_profile('ext_beta:ollama_default')

        self.assertEqual(profile.profile_id, 'ext_beta:ollama_default')
        self.assertEqual(profile.model_ref, 'ollama/beta')


if __name__ == '__main__':
    unittest.main()
