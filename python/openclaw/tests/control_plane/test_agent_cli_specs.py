from __future__ import annotations

import unittest

from openclaw.specs.agent_cli import with_registered_agent_runner_prefix


class AgentCliSpecsTest(unittest.TestCase):
    def test_registered_agent_runner_prefix_preserves_passthrough_separator(self) -> None:
        rendered = with_registered_agent_runner_prefix('demo run --json', 'demo')

        self.assertEqual(
            rendered,
            'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime scheduler-run-agent-runtime --agent-ref demo -- run --json',
        )

    def test_registered_agent_runner_prefix_keeps_bare_agent_runner_without_separator(self) -> None:
        rendered = with_registered_agent_runner_prefix('demo', 'demo')

        self.assertEqual(
            rendered,
            'bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime scheduler-run-agent-runtime --agent-ref demo',
        )


if __name__ == '__main__':
    unittest.main()
