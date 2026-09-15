import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from recar.evidence_report import build
from recar.recovery_policy import (
    MINIMUM_STABILIZATION_SECONDS,
    may_continue_after_case,
    recovery_failures,
    require_restoration_before_reset,
    wait_for_minimum_stabilization,
)


def result_for(case, calibration):
    return {
        "injection_start_monotonic": 10.0,
        "selector_restored_monotonic": 12.0,
        "vector_connection_epoch_s": 1000.0,
        "daq_period_ms": 10,
        "daq_sample_count": 221,
        "injection": "READBACK_VERIFIED",
        "selector_restore": "READBACK_VERIFIED",
        "recovery": "BASELINE_VERIFIED",
        "warning_observed": False,
        "test_case": case,
        "calibration_evidence": calibration,
    }


def write_capture(directory):
    frames = [
        {"id": identifier, "rx": True, "extended": False, "timestamp": 1001.0 + offset,
         "monotonic": 10.0 + offset, "data": "0101010101010101", "channel": "CAN1", "dlc": 8}
        for identifier, offset in ((0x11A, 0.0), (0x11B, 0.01), (0x11C, 0.02))
    ]
    rows = [
        {"monotonic": 8.9 + index / 100, "timestamp0": index, "timestamp1": 0,
         "signals": {"ibus": 0.1, "EcuStatus": 9.0, "IgnStatus": 1.0}}
        for index in range(221)
    ]
    (directory / "can.jsonl").write_text("\n".join(json.dumps(row) for row in frames), encoding="utf-8")
    (directory / "daq.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


class ReportTests(unittest.TestCase):
    def test_single_signal_report_still_renders(self):
        case = {"selection_id": 1, "excel_row": 7, "tsr_id": "FN-20763",
                "expected_fault": "SENT_FAILURE", "injection_signal": "SENT", "injection_value": 1,
                "fhti_ms": 24, "fdti_ms": 20, "writes": []}
        calibration = {"original": {"data": "00"}, "injection": {"readback_data": "01"},
                       "restoration": {"readback_data": "00"}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); write_capture(out)
            build(out, result_for(case, calibration))
            html = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn("SENT = 1", html)
        self.assertIn("XCP bytes: original 00", html)
        self.assertIn("FDTI_PASS", html)
        self.assertIn("FHTI_PASS", html)
        self.assertIn("Final functional result", html)
        self.assertIn("Evidence only; no functional verdict effect", html)

    def test_multi_signal_null_metadata_and_ordered_evidence_render(self):
        case = {"selection_id": 1138, "excel_row": 138, "tsr_id": "FN-20999",
                "expected_fault": "PREDRIVER_FAILURE", "injection_signal": None,
                "injection_value": None, "fhti_ms": 20, "fdti_ms": 16,
                "writes": [{"signal": "ENABLE", "value": 1, "order": 1},
                           {"signal": "SELECTOR", "value": 1, "order": 2}]}
        calibration = {
            "originals": [{"signal": "ENABLE", "data": "07"}, {"signal": "SELECTOR", "data": "09"}],
            "writes": [{"signal": "ENABLE", "value": 1, "order": 1, "readback_data": "01", "verified": True},
                       {"signal": "SELECTOR", "value": 1, "order": 2, "readback_data": "01", "verified": True}],
            "restoration": [{"signal": "SELECTOR", "readback_data": "09", "verified": True},
                            {"signal": "ENABLE", "readback_data": "07", "verified": True}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); write_capture(out)
            result = result_for(case, calibration)
            result["hardware_classification"] = "HARDWARE_EXECUTED_INCOMPLETE"
            build(out, result)
            html = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn("Ordered injection", html)
        self.assertLess(html.index("ENABLE"), html.index("SELECTOR"))
        self.assertIn("Reverse restoration evidence", html)
        self.assertIn("N/A", html)
        self.assertIn("HARDWARE_EXECUTED_INCOMPLETE", html)

    def test_repeated_signal_sequence_renders_once_in_restoration(self):
        case = {"selection_id": 1215, "excel_row": 215, "tsr_id": "FN-X",
                "expected_fault": "PDC", "injection_signal": None, "injection_value": None,
                "fhti_ms": None, "fdti_ms": None,
                "writes": [{"signal": "X", "value": 1, "order": 1},
                           {"signal": "X", "value": 3, "order": 2}]}
        calibration = {"originals": [{"signal": "X", "data": "09"}],
                       "writes": [{"signal": "X", "value": 1, "order": 1, "verified": True},
                                  {"signal": "X", "value": 3, "order": 2, "verified": True}],
                       "restoration": [{"signal": "X", "readback_data": "09", "verified": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); write_capture(out)
            build(out, result_for(case, calibration))
            html = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn(">1<", html)
        self.assertIn(">3<", html)
        restoration = html.split("Reverse restoration evidence", 1)[1]
        self.assertEqual(restoration.count(">X<"), 1)


def recovered_result():
    return {
        "selector_restore": "READBACK_VERIFIED",
        "calibration_evidence": {"restoration": {"verified": True}},
        "recovery": "BASELINE_VERIFIED",
        "capture_errors": [],
        "recovery_evidence": {
            "hard_reset_request": "SENT",
            "stabilization": {"elapsed_seconds": 5.0},
            "xcp_reconnect": "COMPLETED",
            "xcp_unlock": "COMPLETED",
            "restored_originals_verified": True,
            "fresh_pretrigger": {"status": "PASSED", "fresh": True,
                                 "acquisition_scope": "CURRENT_CONNECTION_ONLY"},
            "can_traffic_fresh": True,
        },
    }


class RecoveryPolicyTests(unittest.TestCase):
    def test_hard_reset_is_blocked_until_restoration_verified(self):
        with self.assertRaisesRegex(RuntimeError, "restoration"):
            require_restoration_before_reset({"calibration_evidence": {}})
        require_restoration_before_reset(recovered_result())

    def test_minimum_five_second_stabilization(self):
        clock = SimpleNamespace(value=0.0)
        def monotonic(): return clock.value
        def sleep(seconds): clock.value += seconds
        evidence = wait_for_minimum_stabilization(sleep=sleep, monotonic=monotonic)
        self.assertGreaterEqual(evidence["elapsed_seconds"], MINIMUM_STABILIZATION_SECONDS)

    def test_functional_fail_or_incomplete_can_continue_after_safe_recovery(self):
        result = recovered_result()
        self.assertTrue(may_continue_after_case("PRODUCT_FAIL", result))
        self.assertTrue(may_continue_after_case("INCOMPLETE", result))

    def test_baseline_or_fresh_post_reset_pretrigger_failure_stops_next_case(self):
        result = recovered_result(); result["recovery"] = "BASELINE_NOT_VERIFIED"
        self.assertFalse(may_continue_after_case("INCOMPLETE", result))
        self.assertIn("baseline", recovery_failures(result))
        result = recovered_result()
        result["recovery_evidence"]["fresh_pretrigger"]["acquisition_scope"] = "PREVIOUS_CONNECTION"
        self.assertFalse(may_continue_after_case("COMPLETED", result))
        self.assertIn("fresh_pretrigger", recovery_failures(result))

    def test_xcp_or_can_recovery_failure_stops_next_case(self):
        result = recovered_result(); result["recovery_evidence"]["xcp_unlock"] = "FAILED"
        result["recovery_evidence"]["can_traffic_fresh"] = False
        self.assertFalse(may_continue_after_case("PRODUCT_FAIL", result))
        self.assertIn("xcp_unlock", recovery_failures(result))
        self.assertIn("can_communication", recovery_failures(result))
