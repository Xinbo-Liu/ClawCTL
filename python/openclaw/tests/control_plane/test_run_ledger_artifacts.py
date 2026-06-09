from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from openclaw.control_plane import run_ledger
from openclaw.tests.support.helpers import isolated_test_root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RunLedgerArtifactEvidenceTest(unittest.TestCase):
    def _job(self) -> dict[str, object]:
        return {
            "id": "job_alpha",
            "qualifiedId": "agent_demo:job_alpha",
            "resolvedRuntimeJobKey": "agent_demo:job_alpha",
            "resolvedOutputs": {
                "artifacts": ["business_output_json"],
                "statusSignals": ["business_output_ready"],
            },
            "artifactPolicy": {
                "runArtifactRoot": "demo_artifact_dir",
                "latestAlias": "latest_job_alpha",
                "retentionDays": 14,
            },
        }

    def test_structured_stdout_is_run_evidence_when_business_artifact_is_noop(self) -> None:
        with isolated_test_root("run-ledger-stdout-evidence") as root:
            artifact_root = root / "artifacts"
            artifact_root.mkdir(parents=True)
            stdout_log = root / "run" / "stdout.log"
            stdout_log.parent.mkdir(parents=True)
            stdout_log.write_text(json.dumps({"status": "dry_run", "reason": "no_pending_task"}) + "\n", encoding="utf-8")

            with mock.patch.object(run_ledger, "resolve_artifact_root", return_value=artifact_root):
                manifest = run_ledger.build_artifacts_manifest(
                    job=self._job(),
                    run_id="run-1",
                    stdout_log_path=stdout_log,
                    result_status="succeeded",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                )

        self.assertEqual(manifest["acceptance"]["status"], "pass")
        self.assertTrue(manifest["acceptance"]["evidencePresent"])
        self.assertEqual(["scheduler_structured_stdout"], manifest["acceptance"]["evidenceSources"])
        self.assertEqual("structured_stdout_json", manifest["schedulerEntries"][0]["evidenceKind"])
        self.assertEqual(1, manifest["schedulerEntries"][0]["jsonLineCount"])

    def test_unstructured_stdout_does_not_satisfy_declared_output_evidence(self) -> None:
        with isolated_test_root("run-ledger-unstructured-stdout") as root:
            artifact_root = root / "artifacts"
            artifact_root.mkdir(parents=True)
            stdout_log = root / "run" / "stdout.log"
            stdout_log.parent.mkdir(parents=True)
            stdout_log.write_text("plain log line\n", encoding="utf-8")

            with mock.patch.object(run_ledger, "resolve_artifact_root", return_value=artifact_root):
                manifest = run_ledger.build_artifacts_manifest(
                    job=self._job(),
                    run_id="run-1",
                    stdout_log_path=stdout_log,
                    result_status="succeeded",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                )

        self.assertEqual(manifest["acceptance"]["status"], "fail")
        self.assertIn("declared_outputs_without_observed_evidence", manifest["acceptance"]["reasons"])
        self.assertEqual([], manifest["schedulerEntries"])

    def test_artifact_root_recent_file_remains_primary_evidence(self) -> None:
        with isolated_test_root("run-ledger-artifact-root-evidence") as root:
            artifact_root = root / "artifacts"
            artifact_root.mkdir(parents=True)
            (artifact_root / "business_output.json").write_text("{}", encoding="utf-8")
            stdout_log = root / "run" / "stdout.log"
            stdout_log.parent.mkdir(parents=True)
            stdout_log.write_text(json.dumps({"status": "ok"}) + "\n", encoding="utf-8")

            with mock.patch.object(run_ledger, "resolve_artifact_root", return_value=artifact_root):
                manifest = run_ledger.build_artifacts_manifest(
                    job=self._job(),
                    run_id="run-1",
                    stdout_log_path=stdout_log,
                    result_status="succeeded",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                )

        self.assertEqual(manifest["acceptance"]["status"], "pass")
        self.assertEqual(["artifact_root_recent_file"], manifest["acceptance"]["evidenceSources"])
        self.assertEqual(["business_output.json"], [item["relativePath"] for item in manifest["observedEntries"]])
        self.assertEqual([], manifest["schedulerEntries"])

    def test_structured_stdout_does_not_hide_missing_artifact_root(self) -> None:
        with isolated_test_root("run-ledger-missing-root") as root:
            stdout_log = root / "run" / "stdout.log"
            stdout_log.parent.mkdir(parents=True)
            stdout_log.write_text(json.dumps({"status": "ok"}) + "\n", encoding="utf-8")

            with mock.patch.object(run_ledger, "resolve_artifact_root", return_value=root / "missing"):
                manifest = run_ledger.build_artifacts_manifest(
                    job=self._job(),
                    run_id="run-1",
                    stdout_log_path=stdout_log,
                    result_status="succeeded",
                    started_at=_now_iso(),
                    finished_at=_now_iso(),
                )

        self.assertEqual(manifest["acceptance"]["status"], "fail")
        self.assertIn("artifact_root_missing:demo_artifact_dir", manifest["acceptance"]["reasons"])
        self.assertTrue(manifest["acceptance"]["evidencePresent"])


if __name__ == "__main__":
    unittest.main()
