import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recar.batch import SENT_ALL_ROWS, SENT_ALL_VALUES, failures, resolve_named_batch


def safe_result(functional_status="COMPLETED"):
    warning = functional_status != "INCOMPLETE"
    frames = [
        {
            "id": identifier,
            "timestamp": 100.0 + offset,
            "vector_elapsed_s": 1.0 + offset,
        }
        for identifier, offset in ((0x11A, 0.0), (0x11B, 0.01), (0x11C, 0.02))
    ]
    return {
        "baseline": "VERIFIED",
        "pretest_baseline_status": "PRETEST_BASELINE_VERIFIED",
        "injection": "READBACK_VERIFIED",
        "selector_restore": "READBACK_VERIFIED",
        "warning_observed": warning,
        "reset_positive_response": {"id": 0x7F8, "data": "0251010000000000"},
        "recovery": "BASELINE_VERIFIED",
        "posttest_baseline_status": "POSTTEST_BASELINE_VERIFIED",
        "daq_period_ms": 10,
        "daq_sample_count": 200,
        "pretrigger_evidence": {
            "status": "PASSED",
            "fresh": True,
            "sample_count": 130,
            "maximum_gap_ms": 12.0,
            "acquisition_scope": "CURRENT_CONNECTION_ONLY",
        },
        "evidence": {
            "window_complete": True,
            "max_sample_gap_ms": 12.0,
            "event_frames": frames if warning else [],
            "timing": {
                "pairing": "UNIQUE_ORDERED_TRIPLET" if warning else "MISSING_EVENTS",
                **(
                    {
                        "11B_minus_11A_ms": 10.0,
                        "11C_minus_11B_ms": 10.0,
                        "11C_minus_11A_ms": 20.0,
                    }
                    if warning
                    else {}
                ),
            },
        },
        "functional_execution": {
            "status": functional_status,
            "timing_verdict": "PASS" if functional_status == "COMPLETED" else "NOT_EVALUATED",
        },
        "calibration_evidence": {"restoration": {"verified": True}},
        "recovery_evidence": {
            "hard_reset_request": "SENT",
            "stabilization": {"elapsed_seconds": 5.1},
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


def run_mock_batch(root, selected, result_factory):
    from recar.batch import main

    calls = []

    def launch(command, **kwargs):
        selection_id = int(command[-1])
        calls.append(selection_id)
        report = root / f"case_{selection_id}"
        report.mkdir()
        result = result_factory(selection_id)
        (report / "result.json").write_text(json.dumps(result))
        (report / "report.html").write_text("<html>case report</html>")
        kwargs["stdout"].write(str(report) + "\n")
        return SimpleNamespace(returncode=0)

    with (
        patch("recar.batch.ROOT", root),
        patch("recar.batch.subprocess.run", side_effect=launch),
        patch("builtins.print"),
    ):
        main(["--execute-hardware", "--cases", *map(str, selected)])
    report = next(root.glob("reports/batch/*"))
    return calls, json.loads((report / "batch_summary.json").read_text()), report


class BatchTests(unittest.TestCase):
    def test_sent_all_is_exact_catalog_mapping_and_order(self):
        cases = resolve_named_batch("sent-all")
        self.assertEqual(tuple(case.excel_row for case in cases), SENT_ALL_ROWS)
        self.assertEqual(tuple(case.injection_value for case in cases), SENT_ALL_VALUES)
        self.assertEqual(tuple(case.selection_id for case in cases), SENT_ALL_VALUES)
        self.assertEqual(
            {case.injection_signal for case in cases},
            {"FAULT_INJECT.Sent_Fault_Test"},
        )

    def test_named_batch_command_routes_all_16_in_order(self):
        from recar.batch import main

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def launch(command, **kwargs):
                selection_id = int(command[-1])
                calls.append(selection_id)
                report = root / f"case_{selection_id}"
                report.mkdir()
                (report / "result.json").write_text(json.dumps(safe_result()))
                (report / "report.html").write_text("<html>case report</html>")
                kwargs["stdout"].write(str(report) + "\n")
                return SimpleNamespace(returncode=0)

            with (
                patch("recar.batch.ROOT", root),
                patch("recar.batch.subprocess.run", side_effect=launch),
                patch("builtins.print"),
            ):
                main(["--execute-hardware", "--batch", "sent-all"])
            summary = json.loads(
                next(root.glob("reports/batch/*/batch_summary.json")).read_text()
            )
        self.assertEqual(calls, list(range(1, 17)))
        self.assertEqual(summary["status"], "BATCH_COMPLETED_ALL_PASS")
        self.assertEqual(summary["aggregate"]["requested"], 16)
        self.assertEqual(summary["aggregate"]["PASS"], 16)

    def test_continues_after_product_fail_and_incomplete(self):
        statuses = {1: "PRODUCT_FAIL", 2: "INCOMPLETE", 3: "COMPLETED"}
        with tempfile.TemporaryDirectory() as tmp:
            calls, summary, _ = run_mock_batch(
                Path(tmp), [1, 2, 3], lambda selection: safe_result(statuses[selection])
            )
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(summary["status"], "BATCH_COMPLETED_WITH_FAILURES")
        self.assertEqual(summary["aggregate"]["PRODUCT_FAIL"], 1)
        self.assertEqual(summary["aggregate"]["INCOMPLETE"], 1)

    def test_abort_after_restoration_failure_marks_remaining_not_run(self):
        def result_factory(selection):
            result = safe_result()
            if selection == 1:
                result["selector_restore"] = "FAILED"
                result["calibration_evidence"]["restoration"]["verified"] = False
            return result

        with tempfile.TemporaryDirectory() as tmp:
            calls, summary, _ = run_mock_batch(Path(tmp), [1, 2, 3], result_factory)
        self.assertEqual(calls, [1])
        self.assertEqual(summary["status"], "BATCH_ABORTED_INFRASTRUCTURE")
        self.assertEqual(summary["rows"][0]["classification"], "BLOCKED_INFRASTRUCTURE")
        self.assertEqual(
            [row["classification"] for row in summary["rows"][1:]],
            ["NOT_RUN_BATCH_ABORTED", "NOT_RUN_BATCH_ABORTED"],
        )

    def test_abort_after_posttest_baseline_failure(self):
        def result_factory(selection):
            result = safe_result()
            result["recovery"] = "BASELINE_NOT_VERIFIED"
            result["posttest_baseline_status"] = "POSTTEST_BASELINE_NOT_VERIFIED"
            return result

        with tempfile.TemporaryDirectory() as tmp:
            calls, summary, _ = run_mock_batch(Path(tmp), [1, 2], result_factory)
        self.assertEqual(calls, [1])
        self.assertEqual(summary["aggregate"]["NOT_RUN"], 1)
        self.assertGreater(summary["aggregate"]["restoration_recovery_failures"], 0)

    def test_missing_pretest_baseline_or_fresh_daq_prevents_next_case(self):
        def result_factory(selection):
            result = safe_result()
            result["pretest_baseline_status"] = "NOT_VERIFIED"
            result["pretrigger_evidence"]["acquisition_scope"] = "PREVIOUS_CONNECTION"
            return result

        with tempfile.TemporaryDirectory() as tmp:
            calls, summary, _ = run_mock_batch(Path(tmp), [1, 2], result_factory)
        self.assertEqual(calls, [1])
        self.assertIn("pretest_baseline", summary["rows"][0]["recovery_failures"])
        self.assertIn("pretest_fresh_daq", summary["rows"][0]["recovery_failures"])
        self.assertEqual(summary["rows"][1]["classification"], "NOT_RUN_BATCH_ABORTED")

    def test_summary_has_all_required_evidence_and_individual_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls, summary, report = run_mock_batch(
                Path(tmp), [1], lambda _: safe_result()
            )
            html = (report / "batch_summary.html").read_text()
            report_files = [path.name for path in report.iterdir()]
        row = summary["rows"][0]
        self.assertEqual(calls, [1])
        self.assertEqual(row["pretest_baseline"], "PRETEST_BASELINE_VERIFIED")
        self.assertEqual(row["posttest_baseline"], "POSTTEST_BASELINE_VERIFIED")
        self.assertEqual(row["events"]["A"]["count"], 1)
        self.assertGreaterEqual(row["stabilization_seconds"], 5.0)
        self.assertIn("batch_summary.json", report_files)
        self.assertIn("file:///", html)
        self.assertIn("PRETEST", html)
        self.assertIn("POSTTEST", html)

    def test_hardware_flag_is_required_before_named_batch(self):
        from recar.batch import main

        with self.assertRaises(SystemExit), patch("sys.stderr"):
            main(["--batch", "sent-all"])

    def test_failed_recovery_stops_batch(self):
        result = safe_result()
        result["recovery"] = "BASELINE_NOT_VERIFIED"
        self.assertEqual(failures(result), ["recovery"])

    def test_empty_result_never_completes(self):
        self.assertIn("event_pairing", failures({}))


if __name__ == "__main__":
    unittest.main()
