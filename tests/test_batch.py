import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recar.batch import failures


def safe_result(*, warning_observed=True):
    return {
        "injection": "READBACK_VERIFIED",
        "selector_restore": "READBACK_VERIFIED",
        "warning_observed": warning_observed,
        "reset_positive_response": {"id": 0x7F8},
        "recovery": "BASELINE_VERIFIED",
        "daq_period_ms": 10,
        "daq_sample_count": 200,
        "evidence": {
            "window_complete": True,
            "timing": {
                "pairing": "UNIQUE_ORDERED_TRIPLET"
                if warning_observed
                else "MISSING_EVENTS"
            },
        },
        "calibration_evidence": {"restoration": {"verified": True}},
        "recovery_evidence": {
            "hard_reset_request": "SENT",
            "stabilization": {"elapsed_seconds": 5.0},
            "xcp_reconnect": "COMPLETED",
            "xcp_unlock": "COMPLETED",
            "restored_originals_verified": True,
            "fresh_pretrigger": {
                "status": "PASSED",
                "fresh": True,
                "acquisition_scope": "CURRENT_CONNECTION_ONLY",
            },
            "can_traffic_fresh": True,
        },
    }


class BatchTests(unittest.TestCase):
    def test_batch_continues_after_functional_incomplete_when_recovery_is_safe(self):
        from recar.batch import main

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def launch(command, **kwargs):
                selected = int(command[-1])
                calls.append(selected)
                out = root / f"case_{selected}"
                out.mkdir()
                result = safe_result(warning_observed=False)
                if selected == 2:
                    result["error"] = "XCP timeout"
                (out / "result.json").write_text(json.dumps(result))
                kwargs["stdout"].write(str(out) + "\n")
                return SimpleNamespace(returncode=0)

            with (
                patch("recar.batch.ROOT", root),
                patch("recar.batch.subprocess.run", side_effect=launch),
                patch(
                    "sys.argv",
                    ["batch", "--execute-hardware", "--cases", "1", "2", "3"],
                ),
                patch("builtins.print"),
            ):
                main()

            self.assertEqual(calls, [1, 2])
            summary = json.loads(
                next(root.glob("reports/batch/*/summary.json")).read_text()
            )
            self.assertEqual(summary["cases"][0]["status"], "INCOMPLETE")
            self.assertEqual(summary["cases"][-1]["status"], "BLOCKED")
            self.assertEqual(summary["not_executed_values"], [3])
            self.assertEqual(
                summary["status"], "STOPPED_ON_RECOVERY_OR_INFRASTRUCTURE_FAILURE"
            )

    def test_failed_recovery_stops_batch(self):
        result = safe_result()
        result["recovery"] = "BASELINE_NOT_VERIFIED"
        self.assertEqual(failures(result), ["recovery"])

    def test_empty_result_never_completes(self):
        self.assertIn("event_pairing", failures({}))


if __name__ == "__main__":
    unittest.main()
