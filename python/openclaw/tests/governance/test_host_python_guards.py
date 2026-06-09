from __future__ import annotations

import unittest

from openclaw.guards.host_python_shell_guard import scan_shell_source


class HostPythonShellGuardTest(unittest.TestCase):
    def test_command_substitution_regex_argument_does_not_trigger_false_positive(self) -> None:
        source = """
pattern='(^|[[:space:]>`-])python3?[[:space:]]+((\\./)?(scripts|python|tests)/[^[:space:]]+\\.py([^[:alnum:]_]|$))'
results="$(python_runtime_guard_search_extended_in_paths "$pattern" "${scan_roots[@]}")"
""".strip()

        self.assertEqual(scan_shell_source(source, 'scripts/lib/python_runtime_guard.sh'), [])

    def test_command_substitution_host_python_invocation_is_still_reported(self) -> None:
        source = 'results="$(python ./scripts/demo.py --check)"'

        self.assertEqual(
            scan_shell_source(source, 'scripts/demo.sh'),
            ['scripts/demo.sh:1:results="$(python ./scripts/demo.py --check)"'],
        )

    def test_env_prefix_host_python_invocation_is_reported(self) -> None:
        source = 'env PYTHONUTF8=1 python3 ./scripts/demo.py --check'

        self.assertEqual(
            scan_shell_source(source, 'scripts/demo.sh'),
            ['scripts/demo.sh:1:env PYTHONUTF8=1 python3 ./scripts/demo.py --check'],
        )

    def test_wrapped_shell_command_from_variable_is_reported(self) -> None:
        source = """
CMD='python3 ./scripts/demo.py --check'
bash -lc "$CMD"
""".strip()

        self.assertEqual(
            scan_shell_source(source, 'scripts/demo.sh'),
            ['scripts/demo.sh:2:bash -lc "$CMD"'],
        )

    def test_quoted_heredoc_body_does_not_trigger_false_positive(self) -> None:
        source = """
cat <<'EOF'
python3 ./scripts/demo.py --check
EOF
""".strip()

        self.assertEqual(scan_shell_source(source, 'scripts/demo.sh'), [])

    def test_printf_literal_does_not_trigger_false_positive(self) -> None:
        source = """printf '%s\\n' 'python3 ./scripts/demo.py --check'"""

        self.assertEqual(scan_shell_source(source, 'scripts/demo.sh'), [])


if __name__ == '__main__':
    unittest.main()
