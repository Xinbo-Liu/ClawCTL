from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.platform.shell_pythonpath_contract import build_report
from openclaw.lib.repo.layout import resolve_repo_root

ROOT_DIR = resolve_repo_root(Path(__file__))


class ShellPythonpathContractTest(unittest.TestCase):
    def test_repo_passes_shell_pythonpath_contract(self) -> None:
        payload = build_report(ROOT_DIR)
        self.assertTrue(payload['ok'], msg='\n'.join(payload['offenders']))

    def test_report_flags_raw_repo_local_pythonpath_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            scripts_dir = repo_root / 'scripts'
            scripts_dir.mkdir(parents=True)
            (scripts_dir / 'bad.sh').write_text(
                'bash "$PYTHON_RUNNER" --env "PYTHONPATH=$ROOT_DIR/python:$ROOT_DIR" -- -m openclaw.cli\n',
                encoding='utf-8',
            )

            payload = build_report(repo_root)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['offenderCount'], 1)
        self.assertIn('scripts/bad.sh:1:', payload['offenders'][0])

    def test_report_flags_repo_local_pythonpath_injection_via_any_repo_root_variable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            scripts_dir = repo_root / 'scripts'
            scripts_dir.mkdir(parents=True)
            (scripts_dir / 'bad.sh').write_text(
                'PYTHONPATH="$SOME_OTHER_ROOT/python:$SOME_OTHER_ROOT${PYTHONPATH:+:$PYTHONPATH}" python -m openclaw.cli\n',
                encoding='utf-8',
            )

            payload = build_report(repo_root)

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['offenderCount'], 1)
        self.assertIn('scripts/bad.sh:1:', payload['offenders'][0])

    def test_public_repo_python_env_helper_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            helper_dir = repo_root / 'scripts' / 'lib'
            helper_dir.mkdir(parents=True)
            (helper_dir / 'repo_python_env.sh').write_text(
                'PYTHONPATH="$REPO_ROOT/python:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"\n',
                encoding='utf-8',
            )

            payload = build_report(repo_root)

        self.assertTrue(payload['ok'], msg='\n'.join(payload['offenders']))
