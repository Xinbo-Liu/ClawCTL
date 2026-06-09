from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openclaw.doctor.platform import centos7_host_shell_guard


class Centos7HostShellGuardTest(unittest.TestCase):
    def test_current_guard_surface_passes(self) -> None:
        report = centos7_host_shell_guard.evaluate()

        self.assertTrue(report['ok'], report['findings'])
        self.assertGreaterEqual(report['targetCount'], 1)
        self.assertGreaterEqual(report['ruleCount'], 1)

    def test_guard_rejects_unsupported_host_shell_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            script = root / 'scripts' / 'setup' / 'prepare_docker_host.sh'
            script.parent.mkdir(parents=True)
            script.write_text('#!/usr/bin/env bash\nlocal -n bad_ref=target\n', encoding='utf-8')
            config = root / 'guard.json'
            config.write_text(
                json.dumps(
                    {
                        'target_paths': ['scripts/setup/prepare_docker_host.sh'],
                        'bash': {
                            'disallowed_patterns': [
                                {
                                    'id': 'bash_local_nameref',
                                    'description': 'Bash 4.2 不支持 local -n nameref。',
                                    'regex': r'\blocal\s+-n\b',
                                }
                            ]
                        },
                        'jq': {'disallowed_patterns': []},
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            report = centos7_host_shell_guard.evaluate(root, config)

        self.assertFalse(report['ok'])
        self.assertIn('bash_local_nameref', '\n'.join(report['findings']))


if __name__ == '__main__':
    unittest.main()
