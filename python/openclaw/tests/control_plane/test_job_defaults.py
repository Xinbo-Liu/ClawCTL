from __future__ import annotations

import unittest

from openclaw.control_plane.jobs.defaults import infer_job_run_artifact_root


class JobDefaultsTest(unittest.TestCase):
    def test_run_artifact_root_inference_prefers_directory_entries(self) -> None:
        root = infer_job_run_artifact_root(
            capabilities={
                "filesystemWrite": [
                    "sample_audit_jsonl",
                    "sample_outputs_dir",
                    "sample_pipeline_health_json",
                ]
            }
        )

        self.assertEqual("sample_outputs_dir", root)

    def test_run_artifact_root_inference_falls_back_to_legacy_first_entry(self) -> None:
        root = infer_job_run_artifact_root(
            capabilities={
                "filesystemWrite": [
                    "custom_artifact_file",
                    "custom_other_file",
                ]
            }
        )

        self.assertEqual("custom_artifact_file", root)


if __name__ == "__main__":
    unittest.main()
