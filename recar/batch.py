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


def failures(result):
    checks = {
        "injection_readback": result.get("injection") == "READBACK_VERIFIED",
        "selector_restore": result.get("selector_restore") == "READBACK_VERIFIED",
        "warning": result.get("warning_observed") is True,
        "reset_response": bool(result.get("reset_positive_response")),
        "recovery": str(result.get("recovery", "")).startswith("BASELINE_VERIFIED"),
        "window": result.get("evidence", {}).get("window_complete") is True,
        "event_pairing": (
            result.get("evidence", {}).get("timing", {}).get("pairing")
            == "UNIQUE_ORDERED_TRIPLET"
        ),
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
    reasons = failures(result)
    infrastructure = {"injection_readback", "selector_restore", "window", "daq", "errors"}
    status = (
        "BLOCKED"
        if infrastructure.intersection(reasons)
        else "INCOMPLETE"
        if reasons
        else "COMPLETED"
    )
    return {
        "status": status,
        "reasons": reasons,
        "timing_verdict": "NOT_EVALUATED",
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
    if process_returncode or functional_status == "BLOCKED" or isolation_failures:
        return "BLOCKED_INFRASTRUCTURE"
    if functional_status == "COMPLETED":
        return "PASS"
    if functional_status in {"PASS", "PRODUCT_FAIL", "INCOMPLETE"}:
        return functional_status
    return "INCOMPLETE"


def result_entry(case, result, execution_order, report, process_returncode=0):
    timing = result.get("evidence", {}).get("timing", {})
    functional = result.get("functional_execution", {})
    functional_status = functional.get("status") or classify(result)["status"]
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
        "selection_id": case.selection_id,
        "excel_row": case.excel_row,
        "tsr_id": case.tsr_id,
        "selector_value": case.injection_value,
        "expected_fault": case.expected_fault,
        "pretest_baseline": pretest_status,
        "pretrigger": pretrigger,
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
            "verdict": functional.get("timing_verdict", "NOT_EVALUATED"),
        },
        "restoration": "READBACK_VERIFIED" if restoration_ok else "NOT_VERIFIED",
        "hard_reset_response": response_data or ("RECEIVED" if response else "NOT_OBSERVED"),
        "stabilization_seconds": recovery.get("stabilization", {}).get("elapsed_seconds"),
        "posttest_baseline": posttest_status,
        "functional_execution_status": functional_status,
        "classification": batch_classification(
            functional_status, result, process_returncode, isolation_failures
        ),
        "functional_failures": failures(result),
        "recovery_failures": isolation_failures,
        "individual_report": str(report / "report.html"),
        "individual_result": str(report / "result.json"),
    }


def not_run_entry(case, execution_order, aborted):
    return {
        "execution_order": execution_order,
        "selection_id": case.selection_id,
        "excel_row": case.excel_row,
        "tsr_id": case.tsr_id,
        "selector_value": case.injection_value,
        "expected_fault": case.expected_fault,
        "pretest_baseline": "NOT_RUN",
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
            "verdict": "NOT_EVALUATED",
        },
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


def _cell(value, digits=None):
    if value is None:
        return "—"
    if digits is not None and isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def save(out, entries, state, cases, batch_name=None):
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
            f'<td>{row["execution_order"]}</td><td>{row["excel_row"]}</td>'
            f'<td>{_cell(row["tsr_id"])}</td><td>{row["selector_value"]}</td>'
            f'<td>{_cell(row["expected_fault"])}</td><td>{_cell(row["pretest_baseline"])}</td>'
            f'<td>{_cell(row["injection_readback"])}</td><td>{_cell(row["daq"].get("status"))}</td>'
            f'<td>{_cell(row["warning_lamp"])}</td>'
            f'<td>{events["A"]["count"]} / {_cell(events["A"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{events["B"]["count"]} / {_cell(events["B"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{events["C"]["count"]} / {_cell(events["C"].get("first_vector_timestamp"), 6)}</td>'
            f'<td>{_cell(timing["A_to_B_ms"], 3)}</td><td>{_cell(timing["B_to_C_ms"], 3)}</td>'
            f'<td>{_cell(timing["A_to_C_ms"], 3)}</td><td>{_cell(timing["verdict"])}</td>'
            f'<td>{_cell(row["restoration"])}</td><td>{_cell(row["hard_reset_response"])}</td>'
            f'<td>{_cell(row["stabilization_seconds"], 3)}</td>'
            f'<td>{_cell(row["posttest_baseline"])}</td>'
            f'<td>{_cell(row["classification"])}</td><td>{link}</td></tr>'
        )
    totals = summary["aggregate"]
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Recar SENT hardware batch</title>
<style>body{{font:14px system-ui;margin:28px;color:#234}}table{{border-collapse:collapse;font-size:12px}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}.summary{{display:flex;gap:24px;flex-wrap:wrap}}</style>
<h1>Recar SENT hardware batch · 10 ms DAQ</h1><p>{_cell(state)}</p>
<div class="summary"><b>Requested: {totals["requested"]}</b><span>Executed: {totals["executed"]}</span><span>PASS: {totals["PASS"]}</span><span>PRODUCT_FAIL: {totals["PRODUCT_FAIL"]}</span><span>INCOMPLETE: {totals["INCOMPLETE"]}</span><span>BLOCKED_INFRASTRUCTURE: {totals["BLOCKED_INFRASTRUCTURE"]}</span><span>NOT_RUN: {totals["NOT_RUN"]}</span><span>Recovery failures: {totals["restoration_recovery_failures"]}</span></div>
<table><tr><th>Order</th><th>Excel</th><th>TSR</th><th>Value</th><th>Expected fault</th><th>PRETEST</th><th>Injection</th><th>DAQ</th><th>Lamp</th><th>A count/time</th><th>B count/time</th><th>C count/time</th><th>A→B ms</th><th>B→C ms</th><th>A→C ms</th><th>Timing verdict</th><th>Restore</th><th>1101 response</th><th>Stabilize s</th><th>POSTTEST</th><th>Classification</th><th>Evidence</th></tr>{''.join(table_rows)}</table>
<p>Functional outcome and recovery isolation are reported separately. No automatic injection retry is performed.</p></html>'''
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

    out = ROOT / "reports" / "batch" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    out.mkdir(parents=True)
    entries = []
    save(out, entries, "RUNNING", cases, args.batch)
    print("BATCH_REPORT " + str(out), flush=True)

    for index, case in enumerate(cases, 1):
        print(f"START {case.selection_id}: {case.expected_fault}", flush=True)
        result = None
        process_returncode = 1
        try:
            log = out / f"fault_{case.selection_id}.log"
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
            process_returncode = process.returncode
            report = Path(log.read_text(encoding="utf-8").strip().splitlines()[-1])
            result = json.loads((report / "result.json").read_text(encoding="utf-8"))
            entry = result_entry(case, result, index, report, process_returncode)
        except Exception as exc:
            entry = not_run_entry(case, index, False)
            entry.update(
                {
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
            save(out, entries, "BATCH_ABORTED_INFRASTRUCTURE", cases, args.batch)
            print("STOPPED " + str(out), flush=True)
            return
        state = completed_state(entries, len(cases))
        save(out, entries, "RUNNING" if index < len(cases) else state, cases, args.batch)
    print("COMPLETED " + str(out), flush=True)


if __name__ == "__main__":
    main()
