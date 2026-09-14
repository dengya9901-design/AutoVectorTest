import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recar.batch import (
    MAX_PRETRIGGER_REACQUISITIONS,
    SENT_ALL_ROWS,
    SENT_ALL_VALUES,
    execute_case,
    failures,
    resolve_named_batch,
    result_entry,
    save,
)
from recar.daq import EvidenceDaq


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
        "canoe": {"running": True, "engine": 1},
        "xcp_unlock": "COMPLETED",
        "injection_parameters_readable": True,
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


def failed_pretrigger_result(maximum_gap_ms=34.0):
    result = safe_result("BLOCKED")
    result.update(
        {
            "injection": "NOT_EXECUTED",
            "selector_readback": 0,
            "baseline": "VERIFIED",
            "pretest_baseline_status": "NOT_VERIFIED",
            "posttest_baseline_status": "NOT_VERIFIED",
            "pretest_baseline_signals": {
                "motor_state": 9,
                "IgnStatus": 1,
                "EPS_WarningLampSt": 0,
                "VCU_Engine_Running": 1,
            },
            "error": "RuntimeError: DAQ pretrigger has a data gap",
            "error_classification": "INFRASTRUCTURE_ERROR",
            "recovery": "NOT_EXECUTED",
            "reset_positive_response": None,
            "calibration_evidence": {
                "original": {"raw": 0, "data": "00"},
                "restoration": {
                    "verified": True,
                    "expected_data": "00",
                    "readback_data": "00",
                },
            },
            "pretrigger_evidence": {
                "status": "FAILED",
                "fresh": True,
                "sample_count": 130,
                "window_duration_ms": 1290.0,
                "maximum_gap_ms": maximum_gap_ms,
                "allowed_gap_ms": 30.0,
                "gaps_above_allowed": 1,
                "daq_error": None,
                "acquisition_scope": "CURRENT_CONNECTION_ONLY",
            },
            "recovery_evidence": {},
        }
    )
    result.pop("injection_start_monotonic", None)
    return result


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

    def test_first_pretrigger_failure_then_fresh_pass_executes_once(self):
        case = resolve_named_batch("sent-all")[0]
        attempts = [
            (0, Path("failed_window"), failed_pretrigger_result()),
            (0, Path("fresh_window"), safe_result()),
        ]
        with patch("recar.batch.launch_case", side_effect=attempts) as launch:
            entry, _, _ = execute_case(case, 1, Path("batch"), "session-new")
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(entry["classification"], "PASS")
        self.assertTrue(entry["pretrigger_attempts"][0]["no_injection_write_confirmed"])
        self.assertTrue(entry["pretrigger_attempts"][0]["reacquisition_scheduled"])
        self.assertNotEqual(
            entry["pretrigger_attempts"][0]["individual_result"],
            entry["pretrigger_attempts"][1]["individual_result"],
        )

    def test_two_pretrigger_failures_then_third_fresh_pass(self):
        case = resolve_named_batch("sent-all")[0]
        attempts = [
            (0, Path("failed_window_1"), failed_pretrigger_result()),
            (0, Path("failed_window_2"), failed_pretrigger_result()),
            (0, Path("fresh_window_3"), safe_result()),
        ]
        with patch("recar.batch.launch_case", side_effect=attempts) as launch:
            entry, _, _ = execute_case(case, 1, Path("batch"), "session-new")
        self.assertEqual(launch.call_count, 3)
        self.assertEqual(entry["classification"], "PASS")
        self.assertEqual(len(entry["pretrigger_attempts"]), 3)

    def test_all_pretrigger_windows_fail_after_bounded_attempts(self):
        case = resolve_named_batch("sent-all")[0]
        attempts = [
            (0, Path(f"failed_window_{number}"), failed_pretrigger_result())
            for number in range(1, 4)
        ]
        with patch("recar.batch.launch_case", side_effect=attempts) as launch:
            entry, _, _ = execute_case(case, 1, Path("batch"), "session-new")
        self.assertEqual(MAX_PRETRIGGER_REACQUISITIONS, 2)
        self.assertEqual(launch.call_count, 3)
        self.assertEqual(entry["classification"], "BLOCKED_INFRASTRUCTURE")
        self.assertFalse(entry["pretrigger_attempts"][-1]["reacquisition_scheduled"])
        self.assertTrue(
            all(
                attempt["no_injection_write_confirmed"]
                for attempt in entry["pretrigger_attempts"]
            )
        )

    def test_ten_ms_daq_threshold_remains_thirty_ms(self):
        daq = EvidenceDaq.__new__(EvidenceDaq)
        daq.period_ms = 10
        daq.lock = threading.Lock()
        daq.error = None
        daq.rows = [
            {"monotonic": index / 100, "signals": {}} for index in range(121)
        ]
        stats = daq.pretrigger_stats(now=1.2)
        self.assertEqual(stats["allowed_gap_ms"], 30.0)
        self.assertEqual(stats["status"], "PASSED")

    def test_post_injection_infrastructure_failure_is_never_reacquired(self):
        case = resolve_named_batch("sent-all")[0]
        result = safe_result("BLOCKED")
        result["error"] = "RuntimeError: XCP readback failed"
        with patch(
            "recar.batch.launch_case", return_value=(0, Path("written_case"), result)
        ) as launch:
            entry, _, _ = execute_case(case, 1, Path("batch"), "session-new")
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(entry["injection_readback"], "READBACK_VERIFIED")
        self.assertEqual(entry["classification"], "BLOCKED_INFRASTRUCTURE")
        self.assertFalse(entry["pretrigger_attempts"][0]["retry_eligible"])

    def test_resume_from_three_merges_prefix_without_rerunning_it(self):
        from recar.batch import main

        cases = resolve_named_batch("sent-all")
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "original_session"
            previous.mkdir()
            old_reports = [root / "old_case_1", root / "old_case_2"]
            for report in old_reports:
                report.mkdir()
                (report / "report.html").write_text("<html>old report</html>")
                (report / "result.json").write_text("{}")
            prefix = [
                result_entry(
                    case,
                    safe_result(),
                    index,
                    old_reports[index - 1],
                    execution_session_id="original_session",
                )
                for index, case in enumerate(cases[:2], 1)
            ]
            save(
                previous,
                prefix,
                "BATCH_ABORTED_INFRASTRUCTURE",
                cases,
                "sent-all",
                execution_session_id="original_session",
            )

            def launch(command, **kwargs):
                selection_id = int(command[-1])
                calls.append(selection_id)
                report = root / f"resumed_case_{selection_id}"
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
                main(
                    [
                        "--execute-hardware",
                        "--batch",
                        "sent-all",
                        "--resume-from",
                        "3",
                        "--merge-from",
                        str(previous / "batch_summary.json"),
                    ]
                )
            resumed = next(root.glob("reports/batch/*/batch_summary.json"))
            summary = json.loads(resumed.read_text())

        self.assertEqual(calls, list(range(3, 17)))
        self.assertEqual(len(summary["rows"]), 16)
        self.assertEqual(summary["rows"][0]["execution_session_id"], "original_session")
        self.assertEqual(summary["rows"][1]["execution_session_id"], "original_session")
        self.assertEqual(summary["rows"][0]["individual_report"], str(old_reports[0] / "report.html"))
        self.assertNotEqual(summary["rows"][2]["execution_session_id"], "original_session")
        self.assertEqual(summary["status"], "BATCH_COMPLETED_ALL_PASS")

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
