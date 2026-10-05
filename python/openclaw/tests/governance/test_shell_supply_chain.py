from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


class ShellSupplyChainContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        bash = resolve_bash_executable()
        if not bash:
            raise unittest.SkipTest('未找到可用 bash；跳过镜像供应链契约测试')
        cls.bash = Path(bash)

    def test_supply_chain_ignores_newer_git_tag_until_release_manifest_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            digest = 'sha256:142f70fa2751bdedf03648ae427372fff3f92ac0e96ab91abb3824b088c38b7b'
            latest_payload_path = temp_root / 'latest-release.json'
            latest_payload_path.write_text('{"tag_name":"v2026.5.3"}\n', encoding='utf-8')
            deploy_env_path = temp_root / 'deploy.env'
            image_pin_path = temp_root / 'openclaw.env'
            runtime_pin_path = temp_root / 'runtime.env'
            runtime_contract_path = temp_root / 'runtime_contract.json'
            deploy_env_path.write_text('', encoding='utf-8')
            image_pin_path.write_text(
                f'OPENCLAW_OFFICIAL_GATEWAY_IMAGE=ghcr.io/openclaw/openclaw:2026.5.7@{digest}\n',
                encoding='utf-8',
            )
            runtime_pin_path.write_text(
                '\n'.join(
                    [
                        'OPENCLAW_CONTROL_PLANE_IMAGE=docker.invalid/python:3.11@sha256:' + '2' * 64,
                        'OPENCLAW_RUNTIME_PYTHON_IMAGE=docker.invalid/python:3.11@sha256:' + '3' * 64,
                        'NGINX_IMAGE=docker.invalid/nginx:1.28@sha256:' + '4' * 64,
                        '',
                    ]
                ),
                encoding='utf-8',
            )
            runtime_contract_path.write_text(
                json.dumps(
                    {
                        'upstream_release': {
                            'release_discovery': {
                                'github_latest_release_api': 'https://api.github.invalid/repos/openclaw/openclaw/releases/latest',
                                'github_release_url_template': 'https://github.invalid/openclaw/openclaw/releases/tag/v{tag}',
                                'package_url': 'https://github.invalid/openclaw/openclaw/releases',
                            },
                            'image_repositories': {
                                'official_release_image_repo': 'ghcr.io/openclaw/openclaw',
                                'default_official_gateway_image_repo': 'ghcr.io/openclaw/openclaw',
                                'allowed_candidate_image_repos': ['ghcr.io/openclaw/openclaw'],
                            },
                        }
                    },
                    ensure_ascii=False,
                )
                + '\n',
                encoding='utf-8',
            )
            fake_bin = temp_root / 'bin'
            fake_bin.mkdir(parents=True, exist_ok=True)
            fake_jq_py = fake_bin / 'jq.py'
            fake_jq_py.write_text(
                r'''
from __future__ import annotations

import json
import sys
import urllib.parse

args = sys.argv[1:]
arg_values: dict[str, str] = {}
flags: set[str] = set()
filters: list[str] = []
i = 0
while i < len(args):
    item = args[i]
    if item.startswith('-') and item not in ('--arg',):
        flags.add(item)
        i += 1
        continue
    if item == '--arg':
        arg_values[args[i + 1]] = args[i + 2]
        i += 3
        continue
    filters.append(item)
    i += 1

filter_text = filters[0] if filters else ''
if '$value|@uri' in filter_text:
    print(urllib.parse.quote(arg_values.get('value', ''), safe=''))
    raise SystemExit(0)
if filter_text == '.tag_name // empty':
    payload = json.loads(sys.stdin.read() or '{}')
    print(str(payload.get('tag_name') or ''))
    raise SystemExit(0)
if filter_text == '.token // .access_token // empty':
    payload = json.loads(sys.stdin.read() or '{}')
    print(str(payload.get('token') or payload.get('access_token') or ''))
    raise SystemExit(0)
if 'OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API' in filter_text:
    payload = json.loads(open(filters[-1], encoding='utf-8').read())
    release = payload.get('upstream_release') if isinstance(payload.get('upstream_release'), dict) else {}
    discovery = release.get('release_discovery') if isinstance(release.get('release_discovery'), dict) else {}
    repos = release.get('image_repositories') if isinstance(release.get('image_repositories'), dict) else {}
    model = payload.get('model_runtime') if isinstance(payload.get('model_runtime'), dict) else {}
    defaults = model.get('defaults') if isinstance(model.get('defaults'), dict) else {}
    catalog = model.get('catalog') if isinstance(model.get('catalog'), list) else []
    catalog_ids = ','.join(str(row.get('id') or '') for row in catalog if isinstance(row, dict) and row.get('id'))
    allowed_repos = repos.get('allowed_candidate_image_repos') if isinstance(repos.get('allowed_candidate_image_repos'), list) else []
    lines = {
        'OPENCLAW_RUNTIME_CONTRACT_GITHUB_LATEST_RELEASE_API': discovery.get('github_latest_release_api') or '',
        'OPENCLAW_RUNTIME_CONTRACT_GITHUB_RELEASE_URL_TEMPLATE': discovery.get('github_release_url_template') or '',
        'OPENCLAW_RUNTIME_CONTRACT_PACKAGE_URL': discovery.get('package_url') or '',
        'OPENCLAW_RUNTIME_CONTRACT_OFFICIAL_RELEASE_IMAGE_REPO': repos.get('official_release_image_repo') or '',
        'OPENCLAW_RUNTIME_CONTRACT_DEFAULT_OFFICIAL_GATEWAY_IMAGE_REPO': repos.get('default_official_gateway_image_repo') or '',
        'OPENCLAW_RUNTIME_CONTRACT_ALLOWED_CANDIDATE_IMAGE_REPOS_CSV': ','.join(str(item) for item in allowed_repos),
        'OPENCLAW_RUNTIME_CONTRACT_HAS_MODEL_RUNTIME': '1' if defaults.get('primary') else '0',
        'OPENCLAW_RUNTIME_CONTRACT_MODEL_PRIMARY': defaults.get('primary') or '',
        'OPENCLAW_RUNTIME_CONTRACT_MODEL_CATALOG_IDS_CSV': catalog_ids,
    }
    for key, value in lines.items():
        print(f'{key}={value}')
    raise SystemExit(0)
if '-n' in flags:
    def status_or_none(name: str) -> int | None:
        value = arg_values.get(name, '').strip()
        return int(value) if value else None

    def value_or_none(name: str) -> str | None:
        value = arg_values.get(name, '').strip()
        return value or None

    payload = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00Z',
        'scope': arg_values.get('scope', ''),
        'current': {
            'ref': arg_values.get('current_ref', ''),
            'repo': arg_values.get('current_repo', ''),
            'tag': arg_values.get('current_tag', ''),
            'release_version': arg_values.get('current_release', ''),
            'pinned_digest': arg_values.get('current_digest', ''),
            'mirror_repo': arg_values.get('current_repo', ''),
            'mirror_digest_status': status_or_none('current_mirror_status'),
            'mirror_digest': value_or_none('current_mirror_digest'),
            'official_repo': arg_values.get('current_official_repo', ''),
            'official_digest_status': status_or_none('current_official_status'),
            'official_digest': value_or_none('current_official_digest'),
        },
        'release_lookup': None,
        'latest': None,
    }
    if arg_values.get('scope') != 'current-tag':
        payload['release_lookup'] = {
            'status': value_or_none('release_lookup_status'),
            'source': value_or_none('release_lookup_source'),
            'detail': value_or_none('release_lookup_detail'),
        }
        payload['latest'] = {
            'tag': arg_values.get('latest_tag', ''),
            'release_version': arg_values.get('latest_release', ''),
            'mirror_repo': arg_values.get('latest_mirror_repo', ''),
            'mirror_digest_status': status_or_none('latest_mirror_status'),
            'mirror_digest': value_or_none('latest_mirror_digest'),
            'official_repo': arg_values.get('latest_official_repo', ''),
            'official_digest_status': status_or_none('latest_official_status'),
            'official_digest': value_or_none('latest_official_digest'),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0)
raise SystemExit(1)
'''.lstrip(),
                encoding='utf-8',
            )

            def bash_path(path: Path) -> str:
                if os.name != 'nt':
                    return path.as_posix()
                result = subprocess.run(
                    [str(self.bash), '-lc', 'cygpath -u "$1"', 'openclaw-path', str(path)],
                    cwd=ROOT_DIR,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    self.skipTest('无法把 Windows 临时目录转换为 Git Bash 路径')
                return result.stdout.strip()

            cache_dir_for_bash = bash_path(temp_root / 'cache')
            deploy_env_for_bash = bash_path(deploy_env_path)
            image_pin_for_bash = bash_path(image_pin_path)
            runtime_pin_for_bash = bash_path(runtime_pin_path)
            runtime_contract_for_bash = bash_path(runtime_contract_path)
            fake_bin_for_bash = bash_path(fake_bin)
            fake_jq_py_for_bash = bash_path(fake_jq_py)
            python_bin_for_bash = bash_path(Path(sys.executable))
            fake_jq = fake_bin / 'jq'
            fake_jq.write_text(
                '#!/usr/bin/env bash\n'
                f'exec {shlex.quote(python_bin_for_bash)} {shlex.quote(fake_jq_py_for_bash)} "$@"\n',
                encoding='utf-8',
            )
            fake_jq.chmod(fake_jq.stat().st_mode | 0o111)
            latest_payload_url = 'file://' + bash_path(latest_payload_path)
            env = os.environ.copy()
            script = f'''
set -euo pipefail
curl() {{
  local out=''
  local headers=''
  local url=''
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o) out="$2"; shift 2 ;;
      -D) headers="$2"; shift 2 ;;
      -w) shift 2 ;;
      -H|--connect-timeout|--max-time|--max-filesize) shift 2 ;;
      -sS|-L) shift ;;
      *) url="$1"; shift ;;
    esac
  done
  if [[ "$url" == *'/repos/openclaw/openclaw/releases/latest' || "$url" == {shlex.quote(latest_payload_url)} ]]; then
    printf '%s\\n' '{{"tag_name":"v2026.5.3"}}' > "$out"
    printf '200'
    return 0
  fi
  case "$url" in
    *'/manifests/2026.5.3-1')
      printf 'HTTP/2 200\\r\\nDocker-Content-Digest: {digest}\\r\\n\\r\\n' > "$headers"
      printf '%s\\n' '{{"schemaVersion":2}}' > "$out"
      printf '200'
      ;;
    *'/manifests/2026.5.3'|*'/manifests/2026.5.4')
      printf 'HTTP/2 404\\r\\n\\r\\n' > "$headers"
      printf '%s\\n' '{{"errors":[{{"code":"MANIFEST_UNKNOWN"}}]}}' > "$out"
      printf '404'
      ;;
    *)
      printf 'unexpected url: %s\\n' "$url" >&2
      return 22
      ;;
  esac
}}
git() {{
  printf '%s\\t%s\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/v2026.5.3
  printf '%s\\t%s\\n' bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb refs/tags/v2026.5.3-1
  printf '%s\\t%s\\n' cccccccccccccccccccccccccccccccccccccccc refs/tags/v2026.5.4
}}
export -f curl git
export PATH={shlex.quote(fake_bin_for_bash)}:$PATH
export OPENCLAW_REPO_CONTRACTS_FORCE_AWK=1
export IMAGE_ENV_DEPLOY_ENV_PATH={shlex.quote(deploy_env_for_bash)}
export IMAGE_ENV_PIN_FILE={shlex.quote(image_pin_for_bash)}
export IMAGE_ENV_RUNTIME_PIN_FILE={shlex.quote(runtime_pin_for_bash)}
export OPENCLAW_RUNTIME_CONTRACT_PATH={shlex.quote(runtime_contract_for_bash)}
export OPENCLAW_SUPPLY_CHAIN_CACHE_DIR_OVERRIDE={shlex.quote(cache_dir_for_bash)}
export OPENCLAW_GITHUB_RELEASES_URL_OVERRIDE={shlex.quote(latest_payload_url)}
export OPENCLAW_GIT_LS_REMOTE_TIMEOUT_SECONDS=0
export OPENCLAW_CURRENT_REMOTE_DIGEST_OVERRIDE={digest}
export OPENCLAW_CURRENT_OFFICIAL_REMOTE_DIGEST_OVERRIDE={digest}
./scripts/images/check_openclaw_supply_chain.sh --scope latest-stable
'''

            result = subprocess.run(
                [str(self.bash), '-lc', script],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=env,
                capture_output=True,
                check=False,
            )

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, msg=output)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['release_lookup']['source'], 'git-tags')
            self.assertEqual(payload['release_lookup']['detail'], 'corrected-from-github-api:2026.5.3')
            self.assertEqual(payload['latest']['tag'], '2026.5.3-1')
            self.assertEqual(payload['latest']['mirror_digest_status'], 0)
            self.assertEqual(payload['latest']['mirror_digest'], digest)
