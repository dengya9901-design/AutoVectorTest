"""Common isolation policy between hardware fault-injection cases."""
from __future__ import annotations

import time


MINIMUM_STABILIZATION_SECONDS = 5.0
BASELINE_CONDITIONS = (
    "CANoe measurement running",
    "Engine_Running confirmed on CAN",
    "ECU CAN communication fresh",
    "EcuStatus == RUN_STATE (9)",
    "IgnStatus == 1",
    "EPS_WarningLampSt == 0",
    "XCP connected",
    "XCP DAQ/CALPAG unlocked",
    "injection parameters readable and restored to captured originals",
    "fresh 10 ms DAQ pretrigger passed",
    "no active infrastructure error",
)
CONTINUABLE_FUNCTIONAL_STATUSES = frozenset(
    {"COMPLETED", "PASS", "PRODUCT_FAIL", "INCOMPLETE"}
)


def restoration_verified(result):
    calibration = result.get("calibration_evidence", {})
    restoration = calibration.get("restoration")
    if isinstance(restoration, list):
        return bool(restoration) and all(row.get("verified") is True for row in restoration)
    if isinstance(restoration, dict):
        return restoration.get("verified") is True
    return result.get("selector_restore") == "READBACK_VERIFIED"


def require_restoration_before_reset(result):
    if not restoration_verified(result):
        raise RuntimeError("Hard Reset blocked until restoration readback is verified")


def wait_for_minimum_stabilization(
    minimum_seconds=MINIMUM_STABILIZATION_SECONDS, *, sleep=time.sleep, monotonic=time.monotonic
):
    start = monotonic()
    sleep(minimum_seconds)
    completed = monotonic()
    elapsed = completed - start
    if elapsed < minimum_seconds:
        sleep(minimum_seconds - elapsed)
        completed = monotonic()
        elapsed = completed - start
    return {
        "minimum_seconds": minimum_seconds,
        "started_monotonic": start,
        "completed_monotonic": completed,
        "elapsed_seconds": elapsed,
        "status": "COMPLETED" if elapsed >= minimum_seconds else "FAILED",
    }


def recovery_failures(result):
    recovery = result.get("recovery_evidence", {})
    pretrigger = recovery.get("fresh_pretrigger", {})
    checks = {
        "restoration": restoration_verified(result),
        "hard_reset_request": recovery.get("hard_reset_request") == "SENT",
        "minimum_stabilization": (
            recovery.get("stabilization", {}).get("elapsed_seconds", 0)
            >= MINIMUM_STABILIZATION_SECONDS
        ),
        "xcp_reconnect": recovery.get("xcp_reconnect") == "COMPLETED",
        "xcp_unlock": recovery.get("xcp_unlock") == "COMPLETED",
        "restored_originals": recovery.get("restored_originals_verified") is True,
        "fresh_pretrigger": (
            pretrigger.get("status") == "PASSED"
            and pretrigger.get("fresh") is True
            and pretrigger.get("acquisition_scope") == "CURRENT_CONNECTION_ONLY"
        ),
        "can_communication": recovery.get("can_traffic_fresh") is True,
        "baseline": str(result.get("recovery", "")).startswith("BASELINE_VERIFIED"),
        "infrastructure": not any(result.get(key) for key in ("error", "capture_errors")),
    }
    return [name for name, passed in checks.items() if not passed]


def may_continue_after_case(functional_status, result):
    """A product outcome never overrides recovery and baseline isolation."""
    return functional_status in CONTINUABLE_FUNCTIONAL_STATUSES and not recovery_failures(result)
