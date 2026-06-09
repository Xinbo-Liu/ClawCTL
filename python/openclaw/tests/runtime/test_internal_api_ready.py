from __future__ import annotations

import unittest
from unittest import mock

from openclaw.internal_api.routes.health import render_ready


class InternalApiReadyTest(unittest.TestCase):
    def test_zero_extension_registry_is_ready_when_scheduler_is_healthy(self) -> None:
        summary = {
            "counts": {"jobs": 0},
            "scheduler": {"healthy": True, "heartbeatAgeSeconds": 5},
            "extensions": [],
        }
        with mock.patch("openclaw.internal_api.routes.health.render_control_plane_summary", return_value=summary):
            with mock.patch("openclaw.internal_api.routes.health.extension_ready_checks", return_value=[]):
                payload = render_ready()
        self.assertEqual(payload.get("status"), "ready")
        checks = payload.get("checks") or {}
        self.assertEqual(((checks.get("controlPlaneRegistry") or {}).get("ok")), True)
        self.assertEqual(((checks.get("controlPlaneRegistry") or {}).get("jobCount")), 0)


if __name__ == "__main__":
    unittest.main()
