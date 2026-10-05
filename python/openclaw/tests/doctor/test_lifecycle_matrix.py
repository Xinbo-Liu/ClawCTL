"""验证 lifecycle matrix 在同一个隔离副本中完成全部探针。"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from openclaw.doctor.agent_modules import lifecycle_matrix


class LifecycleMatrixTest(unittest.TestCase):
    def test_main_copies_repo_once_and_runs_both_probe_families(self) -> None:
        isolated_root = Path('/isolated/repo')
        attach_payload = {'ok': True, 'steps': ['scaffold', 'attach', 'detach', 'rollback']}
        prune_payload = {'ok': True, 'steps': ['prune', 'drop', 'rollback']}
        stdout = io.StringIO()
        with (
            mock.patch.object(lifecycle_matrix, 'copy_repo_tree', return_value=isolated_root) as copy_repo,
            mock.patch.object(
                lifecycle_matrix.attach_detach,
                '_run_probe_in_repo_copy',
                return_value=attach_payload,
            ) as attach_probe,
            mock.patch.object(
                lifecycle_matrix.prune_drop,
                '_run_probe_in_repo_copy',
                return_value=prune_payload,
            ) as prune_probe,
            redirect_stdout(stdout),
        ):
            exit_code = lifecycle_matrix.main([])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload['ok'])
        self.assertEqual(copy_repo.call_count, 1)
        attach_probe.assert_called_once_with(isolated_root, None, control_plane_profile='')
        prune_probe.assert_called_once_with(isolated_root, None, control_plane_profile='')


if __name__ == '__main__':
    unittest.main()
