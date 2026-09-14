"""Common single-signal functional flow, independent of TSR timing limits."""
import json
import time
import subprocess
import sys
from pathlib import Path

from recar.calibration import XcpCalibrationClient
from recar.catalog import select_case


def inject_and_restore(client, parameter, observe, before_write=None, fault_value=None, *, case=None, evidence=None):
    case = case or select_case(fault_value)
    if case != select_case(case.selection_id):
        raise ValueError('Test definition differs from the enabled catalog')
    if parameter is not None and parameter.name != case.injection_signal:
        raise ValueError('Resolved parameter does not match selected test')
    fault_value = case.injection_value
    original = client.read(parameter)
    if evidence is not None:
        evidence['original'] = {'raw': original.raw_value, 'physical': original.physical_value,
                                'data': original.data.hex(), 'monotonic': time.monotonic()}
    if original.raw_value != 0:
        raise RuntimeError("Selector baseline is not the verified inactive value 0")
    # A DOWNLOAD exception may occur after the ECU accepted the write.
    try:
        if before_write:
            before_write()
        expected, actual, ok = client.write_and_verify(parameter, fault_value, tolerance=0)
        if evidence is not None:
            evidence['injection'] = {'expected_data': expected.data.hex(), 'readback_data': actual.data.hex(),
                                     'verified': bool(ok and actual.data == expected.data), 'monotonic': time.monotonic()}
        if not ok or actual.data != expected.data:
            raise RuntimeError("Injection readback mismatch")
        observe()
    finally:
        expected, actual, ok = client.write_and_verify(parameter, original.physical_value, tolerance=0)
        if evidence is not None:
            evidence['restoration'] = {'expected_data': original.data.hex(), 'readback_data': actual.data.hex(),
                                       'verified': bool(ok and actual.data == original.data), 'monotonic': time.monotonic()}
        if not ok or actual.data != original.data:
            raise RuntimeError("RESTORE_FAILED: selector requires operator attention")


def run(xcp, app, monitor, parameters, frames, out, result, daq=None, *, case):
    import can
    import cantools
    db = cantools.database.load_file(r"C:\recar\Recar_CANoe\recar\Databases\Recar_CAN_FD.dbc")
    client = XcpCalibrationClient(xcp)
    fault_value = case.injection_value
    if daq is None:
        raise RuntimeError('10ms DAQ is required')
    events = (out / "smoke.jsonl").open("w", encoding="utf-8", buffering=1)

    def log(kind, **data):
        record = {"monotonic": time.monotonic(), "event": kind, **data}
        events.write(json.dumps(record) + "\n")

    def sample(phase):
        values = dict(daq.latest()['signals'])
        values['motor_state'] = values['EcuStatus']
        fresh = [f for f in list(frames) if f["id"] == 0x33D and f["rx"]
                 and time.monotonic() - f["monotonic"] < 0.2]
        if not fresh:
            raise RuntimeError("No fresh EPS_1 frame")
        lamp = db.decode_message(0x33D, bytes.fromhex(fresh[-1]["data"]), decode_choices=False)["EPS_WarningLampSt"]
        values["EPS_WarningLampSt"] = lamp
        log(phase, signals=values, lamp_timestamp=fresh[-1]["timestamp"])
        return values

    def wait_normal(phase):
        deadline = time.monotonic() + 8
        consecutive = 0
        while time.monotonic() < deadline:
            values = sample(phase)
            normal = values["motor_state"] == 9 and values["EPS_WarningLampSt"] == 0 and values['IgnStatus'] == 1
            consecutive = consecutive + 1 if normal else 0
            if consecutive >= 5:
                return
            time.sleep(0.05)
        raise RuntimeError("Normal baseline was not observed")

    try:
        # Preserve and validate original selector before requesting RUN.
        if not case.is_multi_signal and client.read(parameters[0]).raw_value != 0:
            raise RuntimeError("Selector nonzero; no fault or engine write performed")
        engine = app.GetBus("CAN").GetSignal(1, "SGM_1", "VCU_Engine_Running")
        log("engine_request", before=engine.Value, requested=1)
        engine.Value = 1
        time.sleep(0.6)
        # Verify actual transmitted SGM_1, not just COM's cached setpoint.
        recent = [f for f in list(frames) if f["id"] == 0x53 and time.monotonic()-f["monotonic"] < 0.5]
        if not recent or db.decode_message(0x53, bytes.fromhex(recent[-1]["data"]), decode_choices=False)["VCU_Engine_Running"] != 1:
            raise RuntimeError("Engine_Running not confirmed on CAN")
        wait_normal("baseline")
        result["baseline"] = "VERIFIED"
        observed = []

        def before_write():
            if daq:
                daq.require_pretrigger()
            result["injection_start_monotonic"] = time.monotonic()
            log("injection_write_start", value=fault_value)

        def observe():
            result["injection"] = "READBACK_VERIFIED"
            log("injection_readback", value=fault_value)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                observed.append(sample("fault"))
                time.sleep(0.01)

        result['calibration_evidence'] = {}
        if case.is_multi_signal:
            from recar.multi_signal import MultiSignalRunner
            MultiSignalRunner(client, parameters[:-2], case).inject_and_restore(observe, before_write,
                                                                                result['calibration_evidence'])
        else:
            inject_and_restore(client, parameters[0], observe, before_write, case=case,
                               evidence=result['calibration_evidence'])
        result["selector_restore"] = "READBACK_VERIFIED"
        result["warning_observed"] = any(v["EPS_WarningLampSt"] in (1,2) for v in observed)
        result["states_observed"] = sorted({v["motor_state"] for v in observed})
        sample("after_selector_restore")
        log("parameters_restored" if case.is_multi_signal else "selector_restored",
            value=None if case.is_multi_signal else 0)
        result["selector_restored_monotonic"] = time.monotonic()
        if daq:
            daq.stop()
        xcp.disconnect()
        result["xcp_disconnected_before_reset"] = True
        sent = time.monotonic()
        log("hard_reset_request", request_id="0x7F0", uds="1101")
        monitor.send(can.Message(arbitration_id=0x7F0, is_extended_id=False,
                                 is_fd=True, bitrate_switch=True, data=bytes.fromhex("02 11 01 00 00 00 00 00")))
        response = None
        deadline = sent + 2
        while time.monotonic() < deadline:
            replies = [f for f in list(frames) if f["id"] == 0x7F8 and f["monotonic"] >= sent and f["rx"]]
            for frame in replies:
                data = bytes.fromhex(frame["data"])
                if data[:3] == bytes.fromhex("02 51 01"):
                    response = frame
                    break
                if data[:4] == bytes.fromhex("03 7f 11 78"):
                    continue
                if data[:3] == bytes.fromhex("03 7f 11"):
                    raise RuntimeError("Hard reset rejected: " + data.hex())
            if response:
                break
            time.sleep(0.02)
        result["reset_positive_response"] = response
        time.sleep(3)
        engine.Value = 1
        log("engine_request_after_reset", requested=1)
        # A reset invalidates the original master/session. Rebuild in a fresh
        # process; the first hardware smoke exposed timeouts on object reuse.
        child = subprocess.run([sys.executable, "-B", "-m", "recar.probe", "--connect", "--verify-recovery", '--case', str(case.selection_id)],
                               cwd=str(Path(__file__).resolve().parents[1]), capture_output=True,
                               text=True, timeout=50)
        (out / "recovery_process.log").write_text(child.stdout + child.stderr, encoding="utf-8")
        path = Path(child.stdout.strip().splitlines()[-1]) / "result.json"
        recovery = json.loads(path.read_text(encoding="utf-8"))
        result["recovery_report"] = str(path)
        result["xcp_reconnect"] = recovery.get("read_probe", "FAILED")
        result["recovery"] = recovery.get("recovery", "FAILED")
        if response is None and result["recovery"] == "BASELINE_VERIFIED":
            result["recovery"] = "BASELINE_VERIFIED_RESET_RESPONSE_MISSING"
    finally:
        events.close()
