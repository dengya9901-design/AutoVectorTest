"""Sequential catalog batches with strict recovery isolation between cases."""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from recar.catalog import choices, select_case
from recar.recovery_policy import may_continue_after_case, recovery_failures


ROOT = Path(__file__).resolve().parents[1]
SENT_ALL_ROWS = tuple(range(7, 16)) + tuple(range(22, 29))
SENT_ALL_VALUES = tuple(range(1, 17))
NAMED_BATCHES = ("sent-all",)
MAX_PRETRIGGER_REACQUISITIONS = 2


def resolve_named_batch(name):
    """Resolve and independently validate a formal catalog-driven batch."""
    if name != "sent-all":
        raise ValueError(f"Unknown named batch: {name}")
    cases = sorted(choices("SENT"), key=lambda case: case.injection_value)
    rows = tuple(case.excel_row for case in cases)
    values = tuple(case.injection_value for case in cases)
    signals = {case.injection_signal for case in cases}
    if (
        len(cases) != 16
        or rows != SENT_ALL_ROWS
        or values != SENT_ALL_VALUES
        or signals != {"FAULT_INJECT.Sent_Fault_Test"}
    ):
        raise RuntimeError("The sent-all catalog mapping is not the validated 16-case set")
    return cases


def no_injection_write_occurred(result):
    calibration = result.get("calibration_evidence", {})
    return (
        result.get("injection") == "NOT_EXECUTED"
        and not result.get("injection_start_monotonic")
        and not calibration.get("injection")
    )


def pretrigger_reacquisition_eligible(case, result):
    """Allow retry only for a proven pre-write DAQ-window quality failure."""
    pretrigger = result.get("pretrigger_evidence", {})
    original = result.get("calibration_evidence", {}).get("original", {})
    restoration = result.get("calibration_evidence", {}).get("restoration", {})
    baseline = result.get("pretest_baseline_signals", {})
    error = str(result.get("error", ""))
    return (
        case.family == "SENT"
        and case.injection_signal == "FAULT_INJECT.Sent_Fault_Test"
        and no_injection_write_occurred(result)
        and result.get("selector_readback") == 0
        and original.get("raw") == 0
        and restoration.get("verified") is True
        and result.get("baseline") == "VERIFIED"
        and result.get("xcp_unlock") == "COMPLETED"
        and result.get("injection_parameters_readable") is True
        and result.get("canoe", {}).get("running") is True
        and result.get("canoe", {}).get("engine") == 1
        and baseline.get("motor_state") == 9
        and baseline.get("IgnStatus") == 1
        and baseline.get("EPS_WarningLampSt") == 0
        and baseline.get("VCU_Engine_Running") == 1
        and pretrigger.get("status") == "FAILED"
        and pretrigger.get("acquisition_scope") == "CURRENT_CONNECTION_ONLY"
        and not pretrigger.get("daq_error")
        and not result.get("daq_error")
        and not result.get("capture_errors")
        and error.startswith("RuntimeError: DAQ ")
        and not any(marker in error for marker in ("XL_ERR_", "XCP", "CAN"))
    )


def _case_field(case, name):
    if isinstance(case, dict):
        return case.get(name)
    return getattr(case, name, None)


def evaluate_timing(case, timing):
    """Evaluate the confirmed A/B/C timing model without adding tolerance.

    A is fault occurrence, B is fault reported, and C is system response.
    FDTI is A->B.  FHTI is the complete A->C handling interval.  B->C is
    retained as the measured FRTI interval, but the catalog has no separate
    FRTI limit.
    """
    result = {
        "mapping": {
            "FDTI": "A_TO_B",
            "FRTI": "B_TO_C",
            "FHTI": "A_TO_C",
        },
        "fdti_ms": timing.get("11B_minus_11A_ms"),
        "frti_ms": timing.get("11C_minus_11B_ms"),
        "fhti_ms": timing.get("11C_minus_11A_ms"),
        "fdti_limit_ms": _case_field(case, "fdti_ms"),
        "fhti_limit_ms": _case_field(case, "fhti_ms"),
        "fdti_verdict": "NOT_EVALUATED",
        "fhti_verdict": "NOT_EVALUATED",
        "timing_verdict": "NOT_EVALUATED",
    }
    if timing.get("pairing") != "UNIQUE_ORDERED_TRIPLET":
        return result
    if result["fdti_ms"] is None or result["fhti_ms"] is None:
        return result
    if result["fdti_limit_ms"] is None or result["fhti_limit_ms"] is None:
        result["timing_verdict"] = "TIMING_RULE_CONFIRMATION_REQUIRED"
        return result
    result["fdti_verdict"] = (
        "FDTI_PASS"
        if result["fdti_ms"] <= result["fdti_limit_ms"]
        else "FDTI_FAIL"
    )
    result["fhti_verdict"] = (
        "FHTI_PASS"
        if result["fhti_ms"] <= result["fhti_limit_ms"]
        else "FHTI_FAIL"
    )
    result["timing_verdict"] = (
        "TIMING_PASS"
        if result["fdti_verdict"] == "FDTI_PASS"
        and result["fhti_verdict"] == "FHTI_PASS"
        else "TIMING_FAIL"
    )
    return result


def failures(result):
    timing = result.get("evidence", {}).get("timing", {})
    timing_result = evaluate_timing(result.get("test_case", {}), timing)
    checks = {
        "injection_readback": result.get("injection") == "READBACK_VERIFIED",
        "selector_restore": result.get("selector_restore") == "READBACK_VERIFIED",
        "reset_response": bool(result.get("reset_positive_response")),
        "recovery": str(result.get("recovery", "")).startswith("BASELINE_VERIFIED"),
        "window": result.get("evidence", {}).get("window_complete") is True,
        "event_pairing": (
            result.get("evidence", {}).get("timing", {}).get("pairing")
            == "UNIQUE_ORDERED_TRIPLET"
        ),
        "timing_evidence": timing_result["timing_verdict"]
        not in {"NOT_EVALUATED", "TIMING_RULE_CONFIRMATION_REQUIRED"},
        "timing": timing_result["timing_verdict"] != "TIMING_FAIL",
        "daq": (
            result.get("daq_period_ms") == 10
            and not result.get("daq_error")
            and result.get("daq_sample_count", 0) > 0
        ),
        "errors": not any(
            result.get(key) for key in ("error", "report_error", "capture_errors")
        ),
    }
    return [name for name, ok in checks.items() if not ok]


def classify(result):
    timing = evaluate_timing(
        result.get("test_case", {}), result.get("evidence", {}).get("timing", {})
    )
    reasons = failures(result)
    evaluation_infrastructure = {
        "injection_readback",
        "selector_restore",
        "window",
        "daq",
        "errors",
    }
    if evaluation_infrastructure.intersection(reasons):
        status = "BLOCKED"
    elif "event_pairing" in reasons or "timing_evidence" in reasons:
        status = "INCOMPLETE"
    elif "timing" in reasons:
        status = "PRODUCT_FAIL"
    else:
        status = "PASS"
    return {
        "status": status,
        "reasons": [
            reason
            for reason in reasons
            if reason in evaluation_infrastructure
            or reason in {"event_pairing", "timing_evidence", "timing"}
        ],
        "fdti_verdict": timing["fdti_verdict"],
        "fhti_verdict": timing["fhti_verdict"],
        "timing_verdict": timing["timing_verdict"],
        "timing_mapping": timing["mapping"],
        "warning_lamp": "SUPPORTING_EVIDENCE_ONLY",
        "ibus": "SUPPORTING_EVIDENCE_ONLY",
        "ecu_status": "SUPPORTING_EVIDENCE_ONLY",
        "dtc": "NOT_IMPLEMENTED",
    }


def event_summary(result, identifier):
    frames = [
        frame
        for frame in result.get("evidence", {}).get("event_frames", [])
        if frame.get("id") == identifier
    ]
    first = frames[0] if frames else {}
    return {
        "count": len(frames),
        "first_vector_timestamp": first.get("timestamp"),
        "first_vector_elapsed_s": first.get("vector_elapsed_s"),
    }


def batch_classification(
    functional_status, result, process_returncode=0, isolation_failures=None
):
    isolation_failures = (
        recovery_failures(result) if isolation_failures is None else isolation_failures
    )
    if process_returncode or functional_status in {"BLOCKED", "BLOCKED_INFRASTRUCTURE"} or isolation_failures:
        return "BLOCKED_INFRASTRUCTURE"
    if functional_status in {"PASS", "COMPLETED", "PRODUCT_FAIL", "INCOMPLETE"}:
        if functional_status == "COMPLETED":
            return "PASS"
        return functional_status
    return "INCOMPLETE"


def result_entry(
    case,
    result,
    execution_order,
    report,
    process_returncode=0,
    *,
    execution_session_id=None,
    pretrigger_attempts=None,
):
    timing = result.get("evidence", {}).get("timing", {})
    evaluated_result = {**result, "test_case": result.get("test_case") or case.metadata()}
    functional = classify(evaluated_result)
    functional_status = functional["status"]
    pretrigger = result.get("pretrigger_evidence", {})
    recovery = result.get("recovery_evidence", {})
    pretest_status = result.get("pretest_baseline_status", "NOT_VERIFIED")
    posttest_status = result.get("posttest_baseline_status", "NOT_VERIFIED")
    isolation_failures = recovery_failures(result)
    if pretest_status != "PRETEST_BASELINE_VERIFIED":
        isolation_failures.append("pretest_baseline")
    if not (
        pretrigger.get("status") == "PASSED"
        and pretrigger.get("fresh") is True
        and pretrigger.get("acquisition_scope") == "CURRENT_CONNECTION_ONLY"
    ):
        isolation_failures.append("pretest_fresh_daq")
    if posttest_status != "POSTTEST_BASELINE_VERIFIED":
        isolation_failures.append("posttest_baseline")
    restoration = result.get("calibration_evidence", {}).get("restoration", {})
    if isinstance(restoration, list):
        restoration_ok = bool(restoration) and all(row.get("verified") for row in restoration)
    else:
        restoration_ok = restoration.get("verified") is True
    response = result.get("reset_positive_response") or {}
    response_data = response.get("data") if isinstance(response, dict) else None
    return {
        "execution_order": execution_order,
        "execution_session_id": execution_session_id,
        "selection_id": case.selection_id,
        "excel_row": case.excel_row,
        "tsr_id": case.tsr_id,
        "selector_value": case.injection_value,
        "expected_fault": case.expected_fault,
        "pretest_baseline": pretest_status,
        "pretrigger": pretrigger,
        "pretrigger_attempts": list(pretrigger_attempts or []),
        "injection_readback": result.get("injection", "NOT_EXECUTED"),
        "daq": {
            "status": "PASSED"
            if (
                result.get("daq_period_ms") == 10
                and result.get("daq_sample_count", 0) > 0
                and not result.get("daq_error")
                and result.get("evidence", {}).get("window_complete") is True
            )
            else "FAILED",
            "period_ms": result.get("daq_period_ms"),
            "sample_count": result.get("daq_sample_count", 0),
            "maximum_window_gap_ms": result.get("evidence", {}).get("max_sample_gap_ms"),
        },
        "warning_lamp": result.get("warning_observed"),
        "events": {
            "A": event_summary(result, 0x11A),
            "B": event_summary(result, 0x11B),
            "C": event_summary(result, 0x11C),
        },
        "timing": {
            "A_to_B_ms": timing.get("11B_minus_11A_ms"),
            "B_to_C_ms": timing.get("11C_minus_11B_ms"),
            "A_to_C_ms": timing.get("11C_minus_11A_ms"),
            "fdti_verdict": functional["fdti_verdict"],
            "fhti_verdict": functional["fhti_verdict"],
            "verdict": functional["timing_verdict"],
        },
        "supporting_evidence": result.get("evidence", {}).get(
            "supporting_signals", {}
        ),
        "restoration": "READBACK_VERIFIED" if restoration_ok else "NOT_VERIFIED",
        "hard_reset_response": response_data or ("RECEIVED" if response else "NOT_OBSERVED"),
        "stabilization_seconds": recovery.get("stabilization", {}).get("elapsed_seconds"),
        "posttest_baseline": posttest_status,
        "functional_execution_status": functional_status,
        "classification": batch_classification(
            functional_status, result, process_returncode, isolation_failures
        ),
        "functional_failures": functional["reasons"],
        "recovery_failures": isolation_failures,
        "individual_report": str(report / "report.html"),
        "individual_result": str(report / "result.json"),
    }


def not_run_entry(case, execution_order, aborted):
    return {
        "execution_order": execution_order,
        "execution_session_id": None,
        "selection_id": case.selection_id,
        "excel_row": case.excel_row,
        "tsr_id": case.tsr_id,
        "selector_value": case.injection_value,
        "expected_fault": case.expected_fault,
        "pretest_baseline": "NOT_RUN",
        "pretrigger_attempts": [],
        "injection_readback": "NOT_RUN",
        "daq": {"status": "NOT_RUN"},
        "warning_lamp": None,
        "events": {
            name: {"count": 0, "first_vector_timestamp": None}
            for name in ("A", "B", "C")
        },
        "timing": {
            "A_to_B_ms": None,
            "B_to_C_ms": None,
            "A_to_C_ms": None,
            "fdti_verdict": "NOT_EVALUATED",
            "fhti_verdict": "NOT_EVALUATED",
            "verdict": "NOT_EVALUATED",
        },
        "supporting_evidence": {},
        "restoration": "NOT_RUN",
        "hard_reset_response": "NOT_RUN",
        "stabilization_seconds": None,
        "posttest_baseline": "NOT_RUN",
        "functional_execution_status": "NOT_RUN",
        "classification": "NOT_RUN_BATCH_ABORTED" if aborted else "NOT_RUN",
        "functional_failures": [],
        "recovery_failures": [],
        "individual_report": None,
        "individual_result": None,
    }


def aggregate(rows, state):
    executed = [row for row in rows if not row["classification"].startswith("NOT_RUN")]
    counts = {
        "requested": len(rows),
        "executed": len(executed),
        "PASS": sum(row["classification"] == "PASS" for row in rows),
        "PRODUCT_FAIL": sum(row["classification"] == "PRODUCT_FAIL" for row in rows),
        "INCOMPLETE": sum(row["classification"] == "INCOMPLETE" for row in rows),
        "BLOCKED_INFRASTRUCTURE": sum(
            row["classification"] == "BLOCKED_INFRASTRUCTURE" for row in rows
        ),
        "NOT_RUN": sum(row["classification"].startswith("NOT_RUN") for row in rows),
        "restoration_recovery_failures": sum(
            bool(row.get("recovery_failures")) for row in executed
        ),
        "batch_completion_status": state,
    }
    return counts


def completed_state(entries, requested_count):
    if len(entries) != requested_count:
        return "BATCH_ABORTED_INFRASTRUCTURE"
    if all(entry["classification"] == "PASS" for entry in entries):
        return "BATCH_COMPLETED_ALL_PASS"
    return "BATCH_COMPLETED_WITH_FAILURES"


def launch_case(case, out, attempt):
    """Launch one isolated single-case process and load its evidence."""
    log = out / f"fault_{case.selection_id}_attempt_{attempt}.log"
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "recar.probe",
                "--connect",
                "--inject",
                "--case",
                str(case.selection_id),
            ],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=150,
        )
    report = Path(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    result = json.loads((report / "result.json").read_text(encoding="utf-8"))
    return process.returncode, report, result


def attempt_record(attempt, report, result, retry_eligible, reacquisition_scheduled):
    pretrigger = result.get("pretrigger_evidence", {})
    return {
        "attempt": attempt,
        "acquisition_scope": pretrigger.get("acquisition_scope"),
        "pretrigger_status": pretrigger.get("status", "NOT_AVAILABLE"),
        "sample_count": pretrigger.get("sample_count"),
        "window_duration_ms": pretrigger.get("window_duration_ms"),
        "maximum_gap_ms": pretrigger.get("maximum_gap_ms"),
        "allowed_gap_ms": pretrigger.get("allowed_gap_ms"),
        "gaps_above_allowed": pretrigger.get("gaps_above_allowed"),
        "daq_error": pretrigger.get("daq_error") or result.get("daq_error"),
        "no_injection_write_confirmed": no_injection_write_occurred(result),
        "selector_readback": result.get("selector_readback"),
        "retry_eligible": retry_eligible,
        "reacquisition_scheduled": reacquisition_scheduled,
        "individual_report": str(report / "report.html"),
        "individual_result": str(report / "result.json"),
    }


def execute_case(case, execution_order, out, execution_session_id):
    """Execute once, allowing only bounded pre-write DAQ reacquisition."""
    attempts = []
    last = None
    for attempt in range(1, MAX_PRETRIGGER_REACQUISITIONS + 2):
        process_returncode, report, result = launch_case(case, out, attempt)
        retry_eligible = (
            process_returncode == 0 and pretrigger_reacquisition_eligible(case, result)
        )
        reacquisition_scheduled = (
            retry_eligible and attempt <= MAX_PRETRIGGER_REACQUISITIONS
        )
        attempts.append(
            attempt_record(
                attempt, report, result, retry_eligible, reacquisition_scheduled
            )
        )
        last = (process_returncode, report, result)
        if reacquisition_scheduled:
            print(
                f"PRETRIGGER_REACQUIRE {case.selection_id} "
                f"attempt {attempt + 1}/{MAX_PRETRIGGER_REACQUISITIONS + 1}",
                flush=True,
            )
            continue
        break
    process_returncode, report, result = last
    entry = result_entry(
        case,
        result,
        execution_order,
        report,
        process_returncode,
        execution_session_id=execution_session_id,
        pretrigger_attempts=attempts,
    )
    return entry, result, process_returncode


def load_resume_prefix(previous_summary, cases, resume_from):
    """Load completed prefix evidence without scheduling it for execution."""
    source = Path(previous_summary).resolve()
    summary = json.loads(source.read_text(encoding="utf-8"))
    expected = [case.selection_id for case in cases]
    if summary.get("batch_name") != "sent-all":
        raise ValueError("Resume evidence is not a sent-all batch summary")
    if summary.get("requested_selection_ids") != expected:
        raise ValueError("Resume evidence does not match the current sent-all catalog")
    if resume_from not in expected:
        raise ValueError("Resume selection is not part of sent-all")
    start = expected.index(resume_from)
    previous_by_id = {row["selection_id"]: dict(row) for row in summary.get("rows", [])}
    source_session = summary.get("execution_session_id") or source.parent.name
    prefix = []
    for index, case in enumerate(cases[:start], 1):
        row = previous_by_id.get(case.selection_id)
        if not row or row.get("classification") not in {
            "PASS",
            "PRODUCT_FAIL",
            "INCOMPLETE",
        }:
            raise ValueError(
                f"Selection {case.selection_id} has no reusable completed evidence"
            )
        if row.get("posttest_baseline") != "POSTTEST_BASELINE_VERIFIED":
            raise ValueError(
                f"Selection {case.selection_id} lacks verified recovery isolation"
            )
        if not row.get("individual_report") or not row.get("individual_result"):
            raise ValueError(f"Selection {case.selection_id} lacks individual evidence links")
        if not Path(row["individual_report"]).is_file() or not Path(
            row["individual_result"]
        ).is_file():
            raise ValueError(
                f"Selection {case.selection_id} individual evidence is unavailable"
            )
        row["execution_order"] = index
        row["execution_session_id"] = row.get("execution_session_id") or source_session
        prefix.append(row)
    return prefix, start, source


def _cell(value, digits=None):
    if value is None:
        return "—"
    if digits is not None and isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def _support_cell(row, name):
    signal = row.get("supporting_evidence", {}).get(name, {})
    if not signal:
        return "—"
    if name == "EcuStatus":
        return _cell(signal.get("unique_values"))
    low, high = signal.get("minimum"), signal.get("maximum")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return f"{low:.3f} … {high:.3f}"
    return "—"


def save(
    out,
    entries,
    state,
    cases,
    batch_name=None,
    *,
    execution_session_id=None,
    resume_from=None,
    merged_from=None,
):
    executed_by_id = {entry["selection_id"]: entry for entry in entries}
    aborted = state == "BATCH_ABORTED_INFRASTRUCTURE"
    rows = [
        executed_by_id.get(case.selection_id)
        or not_run_entry(case, index, aborted)
        for index, case in enumerate(cases, 1)
    ]
    summary = {
        "schema_version": 1,
        "batch_name": batch_name,
        "execution_session_id": execution_session_id or out.name,
        "resume_from": resume_from,
        "merged_from": str(merged_from) if merged_from else None,
        "status": state,
        "requested_selection_ids": [case.selection_id for case in cases],
        "rows": rows,
        "aggregate": aggregate(rows, state),
        # Compatibility fields retained for existing consumers.
        "cases": entries,
        "selected_ids": [case.selection_id for case in cases],
        "not_executed_values": [
            row["selection_id"] for row in rows if row["classification"].startswith("NOT_RUN")
        ],
        "timing_verdict": "PER_CASE",
        "dtc": "NOT_IMPLEMENTED",
    }
    document = json.dumps(summary, indent=2)
    (out / "batch_summary.json").write_text(document, encoding="utf-8")
    (out / "summary.json").write_text(document, encoding="utf-8")

    table_rows = []
    for row in rows:
        events = row["events"]
        timing = row["timing"]
        report = row.get("individual_report")
        link = f'<a href="{Path(report).as_uri()}">Report</a>' if report else "—"
        table_rows.append(
            "<tr>"
            f'<td>{row["execution_order"]}</td><td>{_cell(row.get("execution_session_id"))}</td>'
            f'<td>{row["excel_row"]}</td>'
            f'<td>{_cell(row["tsr_id"])}</td><td>{row["selector_value"]}</td>'
            f'<td>{_cell(row["expected_fault"])}</td>'
            f'<td>{events["A"]["count"]} / {_cell(events["A"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{events["B"]["count"]} / {_cell(events["B"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{events["C"]["count"]} / {_cell(events["C"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{_cell(timing.get("A_to_B_ms"), 3)}</td><td>{_cell(timing.get("B_to_C_ms"), 3)}</td>'
            f'<td>{_cell(timing.get("A_to_C_ms"), 3)}</td>'
            f'<td>{_cell(timing.get("fdti_verdict", "NOT_EVALUATED"))}</td>'
            f'<td>{_cell(timing.get("fhti_verdict", "NOT_EVALUATED"))}</td>'
            f'<td>{_cell(row["classification"])}</td>'
            f'<td>{_cell(row["warning_lamp"])}</td><td>{_support_cell(row, "ibus")}</td>'
            f'<td>{_support_cell(row, "EcuStatus")}</td>'
            f'<td>{_cell(row["pretest_baseline"])}</td>'
            f'<td>{_cell(row["injection_readback"])}</td><td>{_cell(row["daq"].get("status"))}</td>'
            f'<td>{_cell(row["restoration"])}</td><td>{_cell(row["hard_reset_response"])}</td>'
            f'<td>{_cell(row["stabilization_seconds"], 3)}</td>'
            f'<td>{_cell(row["posttest_baseline"])}</td>'
            f'<td>{link}</td></tr>'
        )
    totals = summary["aggregate"]
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Recar SENT hardware batch</title>
<style>body{{font:14px system-ui;margin:28px;color:#234}}table{{border-collapse:collapse;font-size:12px}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}.summary{{display:flex;gap:24px;flex-wrap:wrap}}</style>
<h1>Recar SENT hardware batch · 10 ms DAQ</h1><p>{_cell(state)}</p>
<div class="summary"><b>Requested: {totals["requested"]}</b><span>Executed: {totals["executed"]}</span><span>PASS: {totals["PASS"]}</span><span>PRODUCT_FAIL: {totals["PRODUCT_FAIL"]}</span><span>INCOMPLETE: {totals["INCOMPLETE"]}</span><span>BLOCKED_INFRASTRUCTURE: {totals["BLOCKED_INFRASTRUCTURE"]}</span><span>NOT_RUN: {totals["NOT_RUN"]}</span><span>Recovery failures: {totals["restoration_recovery_failures"]}</span></div>
<table><tr><th>Order</th><th>Session</th><th>Excel</th><th>TSR</th><th>Value</th><th>Expected fault</th><th>A count/time</th><th>B count/time</th><th>C count/time</th><th>A→B ms</th><th>B→C ms</th><th>A→C ms</th><th>FDTI verdict</th><th>FHTI verdict</th><th>Final functional result</th><th>Lamp (evidence)</th><th>Ibus / A (evidence)</th><th>EcuStatus (evidence)</th><th>PRETEST</th><th>Injection</th><th>DAQ</th><th>Restore</th><th>1101 response</th><th>Stabilize s</th><th>POSTTEST</th><th>Evidence</th></tr>{''.join(table_rows)}</table>
<p>FDTI is A→B; FHTI is A→C; B→C is the recorded FRTI interval. Warning Lamp, Ibus, and EcuStatus are supporting evidence only and do not change the functional verdict. Functional outcome and recovery isolation are reported separately. Pre-injection DAQ quality may be reacquired at most twice using entirely new acquisition processes. Fault injections are never retried automatically.</p></html>'''
    (out / "batch_summary.html").write_text(page, encoding="utf-8")
    (out / "summary.html").write_text(page, encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute-hardware",
        action="store_true",
        help="Required acknowledgement before any ECU batch execution",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--batch", choices=NAMED_BATCHES)
    selection.add_argument(
        "--cases",
        nargs="+",
        type=int,
        choices=[case.selection_id for case in choices()],
        help="Explicit catalog selections for controlled engineering use",
    )
    parser.add_argument(
        "--resume-from",
        type=int,
        help="First sent-all catalog selection to execute in this session",
    )
    parser.add_argument(
        "--merge-from",
        type=Path,
        help="Previous sent-all batch_summary.json providing the completed prefix",
    )
    args = parser.parse_args(argv)
    if not args.execute_hardware:
        parser.error("Hardware batch execution requires --execute-hardware")
    cases = (
        resolve_named_batch(args.batch)
        if args.batch
        else [select_case(selection_id) for selection_id in args.cases]
    )
    if len({case.selection_id for case in cases}) != len(cases):
        parser.error("Duplicate selections are not allowed in one batch")
    if args.resume_from is not None and args.batch != "sent-all":
        parser.error("--resume-from is supported only with --batch sent-all")
    if (args.resume_from is None) != (args.merge_from is None):
        parser.error("--resume-from and --merge-from must be supplied together")

    out = ROOT / "reports" / "batch" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    out.mkdir(parents=True)
    session_id = out.name
    entries = []
    start = 0
    merged_from = None
    if args.resume_from is not None:
        try:
            entries, start, merged_from = load_resume_prefix(
                args.merge_from, cases, args.resume_from
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    save(
        out,
        entries,
        "RUNNING",
        cases,
        args.batch,
        execution_session_id=session_id,
        resume_from=args.resume_from,
        merged_from=merged_from,
    )
    print("BATCH_REPORT " + str(out), flush=True)

    for index, case in enumerate(cases[start:], start + 1):
        print(f"START {case.selection_id}: {case.expected_fault}", flush=True)
        result = None
        process_returncode = 1
        try:
            entry, result, process_returncode = execute_case(
                case, index, out, session_id
            )
        except Exception as exc:
            entry = not_run_entry(case, index, False)
            entry.update(
                {
                    "execution_session_id": session_id,
                    "classification": "BLOCKED_INFRASTRUCTURE",
                    "functional_execution_status": "BLOCKED",
                    "error": str(exc),
                    "recovery_failures": ["process_or_evidence"],
                }
            )
        entries.append(entry)
        print(json.dumps(entry), flush=True)

        functional_status = entry["functional_execution_status"]
        safe_to_continue = (
            result is not None
            and process_returncode == 0
            and not entry["recovery_failures"]
            and may_continue_after_case(functional_status, result)
        )
        if not safe_to_continue:
            save(
                out,
                entries,
                "BATCH_ABORTED_INFRASTRUCTURE",
                cases,
                args.batch,
                execution_session_id=session_id,
                resume_from=args.resume_from,
                merged_from=merged_from,
            )
            print("STOPPED " + str(out), flush=True)
            return
        state = completed_state(entries, len(cases))
        save(
            out,
            entries,
            "RUNNING" if index < len(cases) else state,
            cases,
            args.batch,
            execution_session_id=session_id,
            resume_from=args.resume_from,
            merged_from=merged_from,
        )
    print("COMPLETED " + str(out), flush=True)


if __name__ == "__main__":
    main()
