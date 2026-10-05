from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import unittest

from openclaw.doctor.agent_modules.support import resolve_bash_executable
from openclaw.lib.repo.layout import resolve_repo_root
from openclaw.setup.network.tls_hostname import validate_tls_hostname

ROOT_DIR = resolve_repo_root(Path(__file__))


class ShellTlsHostnameContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        bash = resolve_bash_executable()
        if not bash:
            raise unittest.SkipTest('未找到可用 bash；跳过 TLS 主机名契约测试')
        cls.bash = Path(bash)

    def test_tls_hostname_shell_contract_matches_python_truth(self) -> None:
        cases = (
            'openclaw.internal.example',
            'openclaw-internal.example',
            '',
            ' openclaw.internal.example',
            'openclaw.internal.example ',
            'bad_name.internal',
            '*.internal.example',
            'openclaw.internal.',
            '.openclaw.internal',
            'openclaw..internal',
            '192.168.0.10',
            '999.999.999.999',
            'fd00::10',
            'openclaw;return 200',
            'a' * 64 + '.internal',
            'openclaw.内部',
        )
        lib_path = shlex.quote(str(ROOT_DIR / 'scripts' / 'setup' / 'lib' / 'tls_hostname_contract.sh'))
        script = f'''
set -euo pipefail
source {lib_path}
for value in "$@"; do
  if openclaw_tls_hostname_is_valid "$value"; then
    printf 'VALID\\n'
  else
    printf 'INVALID\\n'
  fi
done
'''
        result = subprocess.run(
            [str(self.bash), '-lc', script, 'tls-hostname-contract', *cases],
            cwd=ROOT_DIR,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        expected = ['VALID' if validate_tls_hostname(value) == '' else 'INVALID' for value in cases]
        self.assertEqual(result.stdout.splitlines(), expected)
