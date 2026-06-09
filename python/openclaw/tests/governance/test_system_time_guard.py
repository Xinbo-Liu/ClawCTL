from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root


ROOT_DIR = resolve_repo_root(Path(__file__))


class SystemTimeGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        bash = resolve_bash_executable()
        if not bash:
            raise unittest.SkipTest('未找到可用 bash；跳过 system_time_guard shell 测试')
        cls.bash = Path(bash)

    @classmethod
    def bash_path(cls, path: Path) -> str:
        if os.name != 'nt':
            return str(path)
        result = subprocess.run(
            [str(cls.bash), '-lc', 'cygpath -u "$1"', '_', str(path)],
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout.strip()

    def run_guard(self, body: str, *, curl_cases: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            shell_fixtures = ''
            if curl_cases is not None:
                curl_case_rows = []
                date_case_rows = []
                for url, date_header in curl_cases.items():
                    curl_case_rows.append(
                        f"{shlex.quote(url)}) printf 'HTTP/2 200\\r\\nDate: {date_header}\\r\\n\\r\\n' ;;"
                    )
                    epoch = int(datetime.strptime(date_header, '%a, %d %b %Y %H:%M:%S GMT').replace(tzinfo=timezone.utc).timestamp())
                    date_case_rows.append(f"{shlex.quote(date_header)}) printf '%s\\n' {epoch}; return 0 ;;")
                shell_fixtures = (
                    'curl() {\n'
                    '  local url="${@: -1}"\n'
                    '  case "$url" in\n'
                    f"    {' '.join(curl_case_rows)}\n"
                    '    *) return 7 ;;\n'
                    '  esac\n'
                    '}\n'
                    'date() {\n'
                    '  if [[ "${1:-}" == "-u" && "${2:-}" == "-d" && "${4:-}" == "+%s" ]]; then\n'
                    '    case "${3:-}" in\n'
                    f"      {' '.join(date_case_rows)}\n"
                    '    esac\n'
                    '  fi\n'
                    '  command date "$@"\n'
                    '}\n'
                )
            script_path = tmp_path / 'run_guard.sh'
            script_path.write_text(
                '#!/usr/bin/env bash\n'
                'set -euo pipefail\n'
                f'ROOT_DIR="{self.bash_path(ROOT_DIR)}"\n'
                f'{shell_fixtures}'
                'source "$ROOT_DIR/scripts/lib/system_time_guard.sh"\n'
                f'{body}\n',
                encoding='utf-8',
                newline='\n',
            )
            script_path.chmod(0o755)
            return subprocess.run(
                [str(self.bash), str(script_path)],
                cwd=ROOT_DIR,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )

    def test_reference_epoch_uses_multi_source_quorum_median(self) -> None:
        result = self.run_guard(
            'export OPENCLAW_SYSTEM_TIME_REFERENCE_URLS="https://a/ https://b/ https://c/"\n'
            'system_time_guard_parse_check_args --min-epoch 0 --max-epoch 9999999999 --min-reference-count 2 --max-reference-skew-seconds 120\n'
            'system_time_guard_reference_epoch\n',
            curl_cases={
                'https://a/': 'Mon, 20 Apr 2026 00:00:00 GMT',
                'https://b/': 'Mon, 20 Apr 2026 00:00:30 GMT',
                'https://c/': 'Mon, 20 Apr 2026 00:01:00 GMT',
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[:5],
            [
                str(int(datetime(2026, 4, 20, 0, 0, 30, tzinfo=timezone.utc).timestamp())),
                'https://b/',
                'Mon, 20 Apr 2026 00:00:30 GMT',
                '3',
                '60',
            ],
        )

    def test_reference_epoch_direct_call_uses_default_policy(self) -> None:
        result = self.run_guard(
            'export OPENCLAW_SYSTEM_TIME_REFERENCE_URLS="https://a/ https://b/"\n'
            'system_time_guard_reference_epoch\n',
            curl_cases={
                'https://a/': 'Mon, 20 Apr 2026 00:00:00 GMT',
                'https://b/': 'Mon, 20 Apr 2026 00:00:30 GMT',
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[3:5], ['2', '30'])

    def test_reference_epoch_rejects_single_source_by_default(self) -> None:
        result = self.run_guard(
            'export OPENCLAW_SYSTEM_TIME_REFERENCE_URLS="https://a/"\n'
            'system_time_guard_parse_check_args --min-epoch 0 --max-epoch 9999999999\n'
            'system_time_guard_reference_epoch\n',
            curl_cases={'https://a/': 'Mon, 20 Apr 2026 00:00:00 GMT'},
        )

        self.assertEqual(result.returncode, 24, msg=result.stdout + result.stderr)
        self.assertIn('可用 HTTP Date 参考源不足', result.stderr)

    def test_reference_epoch_rejects_skewed_sources(self) -> None:
        result = self.run_guard(
            'export OPENCLAW_SYSTEM_TIME_REFERENCE_URLS="https://a/ https://b/"\n'
            'system_time_guard_parse_check_args --min-epoch 0 --max-epoch 9999999999 --max-reference-skew-seconds 120\n'
            'system_time_guard_reference_epoch\n',
            curl_cases={
                'https://a/': 'Mon, 20 Apr 2026 00:00:00 GMT',
                'https://b/': 'Mon, 20 Apr 2026 00:10:00 GMT',
            },
        )

        self.assertEqual(result.returncode, 24, msg=result.stdout + result.stderr)
        self.assertIn('HTTP Date 参考源不一致', result.stderr)

    def test_offline_check_rejects_time_after_trusted_window(self) -> None:
        result = self.run_guard(
            'system_time_guard_check --offline --min-epoch 0 --max-epoch 1\n',
        )

        self.assertEqual(result.returncode, 24, msg=result.stdout + result.stderr)
        self.assertIn('晚于最高可信时间', result.stderr)

    def test_check_system_time_entrypoint_preserves_failure_code(self) -> None:
        result = subprocess.run(
            [
                str(self.bash),
                './scripts/doctor/check_system_time.sh',
                '--offline',
                '--min-epoch',
                '0',
                '--max-epoch',
                '1',
            ],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 24, msg=result.stdout + result.stderr)
        self.assertIn('晚于最高可信时间', result.stderr)

    def test_update_rejects_direct_step_larger_than_configured_limit(self) -> None:
        result = self.run_guard(
            'export OPENCLAW_SYSTEM_TIME_REFERENCE_URLS="https://a/ https://b/"\n'
            'system_time_guard_current_epoch() { printf "0\\n"; }\n'
            'system_time_guard_parse_check_args --min-epoch 0 --max-epoch 9999999999 --max-step-seconds 100\n'
            'system_time_guard_step_to_reference_if_needed\n',
            curl_cases={
                'https://a/': 'Mon, 20 Apr 2026 00:00:00 GMT',
                'https://b/': 'Mon, 20 Apr 2026 00:00:30 GMT',
            },
        )

        self.assertEqual(result.returncode, 24, msg=result.stdout + result.stderr)
        self.assertIn('超过最大跳变阈值', result.stderr)


if __name__ == '__main__':
    unittest.main()
