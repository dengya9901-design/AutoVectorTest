"""Common XCP/CAN probe; explicit --inject performs the selected test and recovery."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from recar.a2l_resolver import A2lResolver
from recar.calibration import XcpCalibrationClient
from recar.parameter import CalibrationParameter
from recar.catalog import choices, select_case

ROOT = Path(__file__).resolve().parents[1]
A2L = Path(r"C:\recar\N029X-CANape-20260826\XCP_SAM.a2l")
CFG = Path(r"C:\recar\Recar_CANoe\recar\1.cfg")


def parameter(symbol) -> CalibrationParameter:
    if symbol.conversion_factor is None or symbol.conversion_offset is None:
        raise ValueError(f"Unsupported conversion: {symbol.name}")
    return CalibrationParameter(
        name=symbol.name, symbol_link=symbol.symbol_link, role="recar", side="",
        address=symbol.address, address_ext=symbol.address_extension,
        data_type=symbol.data_type, byte_order=symbol.byte_order,
        conversion_name=symbol.conversion_name, conversion_type=symbol.conversion_type,
        phys_gain=symbol.conversion_factor, phys_offset=symbol.conversion_offset,
        phys_unit=symbol.unit, phys_min=symbol.lower_limit, phys_max=symbol.upper_limit,
        source_file=str(symbol.source_file),
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--inject", action="store_true", help="FN-20763 scalar fault; requires --connect")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--case', type=int, choices=[c.selection_id for c in choices()])
    selection.add_argument('--fault-value', type=int, choices=[c.selection_id for c in choices()],
                           help='Compatibility alias for catalog selection; prefer --case')
    parser.add_argument("--verify-recovery", action="store_true")
    parser.add_argument("--daq", action="store_true")
    parser.add_argument("--daq-period-ms", type=int, choices=(10,), default=10)
    args = parser.parse_args(argv)
    case = select_case(args.case if args.case is not None else args.fault_value)
    write_definitions = case.writes or ()
    names = tuple((write.signal, 'characteristic') for write in write_definitions) or ((case.injection_signal, 'characteristic'),)
    names += (('motor_state', 'measurement'), ('IgnStatus', 'measurement'))
    args.daq = True
    if args.inject:
        args.daq = True
        if args.daq_period_ms != 10:
            parser.error("Fault tests require 10ms DAQ evidence")
    if args.inject and not args.connect:
        parser.error("--inject requires --connect")
    sys.argv = [sys.argv[0]]
    out = ROOT / "reports" / "probe" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True)
    result = {"injection": "NOT_EXECUTED", "recovery": "NOT_EXECUTED",
              "timing": "NOT_EVALUATED", "dtc": "NOT_IMPLEMENTED", "method": "XCP_UPLOAD_POLLING"}
    result['fault_value'] = case.injection_value
    result['test_case'] = case.metadata()
    try:
        with A2lResolver(A2L) as resolver:
            # Resolve first so missing/ambiguous symbols retain meaningful errors.
            write_symbols = []
            for name, _ in names[:-2]:
                symbol = resolver.resolve(name, kind='characteristic')
                row = resolver._find_matches(name, "characteristic")[0][1]
                if row.type != "VALUE" or row.read_only or row.matrix_dim or row.bit_mask:
                    raise RuntimeError("Fault write must be writable scalar VALUE without bit mask: " + name)
                write_symbols.append(symbol)
            result['selector_capability'] = [{'unique': True, 'object_type': 'VALUE', 'writable': True,
                                               'data_type': symbol.data_type} for symbol in write_symbols]
            symbols = write_symbols + [resolver.resolve(name, kind=kind) for name, kind in names[-2:]]
            if not case.is_multi_signal and (write_symbols[0].data_type != "UBYTE" or write_symbols[0].conversion_factor != 1 or write_symbols[0].conversion_offset != 0):
                raise RuntimeError("Unsupported fault selector representation")
            result["symbols"] = [asdict(s) for s in symbols]
            result["parser_compatibility_fixes"] = resolver.parser_compatibility_fixes
            daq_symbols = [resolver.resolve(n, kind="measurement") for n in ("ibus", "EcuStatus", "IgnStatus")] if args.daq else []
            for s in daq_symbols:
                if s.conversion_factor is None or s.conversion_offset is None:
                    raise RuntimeError("Unsupported DAQ conversion: " + s.name)
            if args.daq:
                result["daq_symbols"] = [asdict(s) for s in daq_symbols]
                result["daq_period_ms"] = args.daq_period_ms
                import re
                source = A2L.read_text(encoding="latin-1")
                block = re.search(r'/begin COMPU_VTAB_RANGE mstates "" 13(.*?)/end COMPU_VTAB_RANGE', source, re.S)
                if not block:
                    raise RuntimeError("EcuStatus enum table missing")
                result["state_labels"] = {str(int(a)): label for a,b,label in re.findall(r'(\d+)\s+(\d+)\s+"([^"]+)"',block[1]) if a==b}
        parameters = [parameter(s) for s in symbols]
        if not args.connect:
            result["connection"] = "NOT_REQUESTED"
            return
        import can
        import win32com.client
        from pyxcp.cmdline import ArgumentParser
        from pyxcp.config import create_application, reset_application

        app = win32com.client.Dispatch("CANoe.Application")
        result['canoe'] = {'configuration': app.Configuration.FullName,
                           'running': bool(app.Measurement.Running)}
        if Path(app.Configuration.FullName).resolve() != CFG or not app.Measurement.Running:
            raise RuntimeError("CANoe configuration/running guard failed")
        result["canoe"] = {"configuration": app.Configuration.FullName,
                           "running": bool(app.Measurement.Running),
                           "engine": app.GetBus("CAN").GetSignal(1, "SGM_1", "VCU_Engine_Running").Value}
        conf = out / "pyxcp_conf.py"
        conf.write_text('c = get_config()\nc.Transport.layer = "CAN"\n'
                        'c.Transport.Can.interface = "custom"\n'
                        'c.Transport.Can.can_id_master = 0x70A\n'
                        'c.Transport.Can.can_id_slave = 0x70C\n'
                        'c.Transport.Can.fd = True\n'
                        'c.Transport.Can.max_dlc_required = True\n'
                        'c.Transport.Can.bitrate = 500000\n'
                        'c.Transport.Can.data_bitrate = 2000000\n'
                        f'c.General.seed_n_key_dll = {str(A2L.parent / "SeedNKeyXcp.dll")!r}\n'
                        f'c.General.custom_dll_loader = {str(ROOT / "tools" / "SeedKeyLoader.exe")!r}\n', encoding="utf-8")
        os.environ["PYXCP_CONFIG"] = str(conf)
        reset_application()
        create_application()
        daq = None
        if args.daq:
            from recar.daq import EvidenceDaq
            daq = EvidenceDaq(daq_symbols, out, args.daq_period_ms)
        # CANoe owns the running channel. Never request different bit timing.
        with can.Bus(interface="vector", app_name="CANoe", channel=0, fd=True,
                     data_bitrate=2_000_000) as monitor:
            # python-can Vector timestamps are mapped to Unix seconds.
            # Keep raw timestamps; each monitor connection has its own display zero.
            result["vector_connection_epoch_s"] = time.time()
            result["vector_connection_monotonic_s"] = time.monotonic()
            if monitor.permission_mask:
                raise RuntimeError("Expected CANoe-owned channel without init access")
            stop = threading.Event()
            errors = []
            frames = []

            def capture() -> None:
                try:
                    with (out / "can.jsonl").open("w", encoding="utf-8", buffering=1) as stream:
                        while not stop.is_set():
                            message = monitor.recv(0.05)
                            if message is not None and message.arbitration_id in (0x33D, 0x53, 0x11A, 0x11B, 0x11C, 0x70A, 0x70C, 0x7F0, 0x7F8):
                                record = {"timestamp": message.timestamp,
                                    "vector_elapsed_s": message.timestamp - result["vector_connection_epoch_s"],
                                    "monotonic": time.monotonic(), "id": message.arbitration_id,
                                    "data": message.data.hex(), "fd": message.is_fd, "channel": "CAN1", "dlc": message.dlc,
                                    "extended": message.is_extended_id, "rx": message.is_rx}
                                frames.append(record)
                                stream.write(json.dumps(record) + "\n")
                except Exception as exc:
                    errors.append(str(exc))

            thread = threading.Thread(target=capture)
            thread.start()
            try:
                with can.Bus(interface="vector", app_name="CANoe", channel=0, fd=True,
                             data_bitrate=2_000_000) as bus:
                    ap = ArgumentParser(description="Recar read-only probe")
                    with ap.run(transport_layer_interface=bus, **({"policy": daq} if daq else {})) as xcp:
                        result["connect_response"] = str(xcp.connect())
                        try:
                            result["protection_before"] = dict(xcp.getCurrentProtectionStatus())
                            xcp.cond_unlock("DAQ,CALPAG")
                            result["protection_after"] = dict(xcp.getCurrentProtectionStatus())
                            if daq:
                                daq.setup()
                                daq.start()
                            client = XcpCalibrationClient(xcp)
                            # All observation signals are sampled by 10ms DAQ.
                            # UPLOAD is retained only for scalar calibration readback.
                            time.sleep(3)
                            daq.latest()
                            selector = client.read(parameters[0]).raw_value
                            result['selector_readback'] = selector
                            if case.is_multi_signal:
                                result['parameter_readbacks'] = {parameter.name: client.read(parameter).raw_value for parameter in parameters[:-2]}
                            with daq.lock:
                                samples = list(daq.rows)
                            with (out / "samples.jsonl").open("w", encoding="utf-8", buffering=1) as stream:
                                for record in samples:
                                    stream.write(json.dumps(record) + "\n")
                            result["read_probe"] = "COMPLETED"
                            if args.verify_recovery:
                                import cantools
                                db = cantools.database.load_file(r"C:\recar\Recar_CANoe\recar\Databases\Recar_CAN_FD.dbc")
                                normal = (case.is_multi_signal or selector == 0) and len(samples) >= 50
                                for sample in samples[-50:]:
                                    v = sample["signals"]
                                    lamp_frames = [f for f in frames if f["id"] == 0x33D and f["rx"] and 0 <= sample["monotonic"]-f["monotonic"] < 0.2]
                                    engine_frames = [f for f in frames if f["id"] == 0x53 and 0 <= sample["monotonic"]-f["monotonic"] < 0.5]
                                    normal &= v["EcuStatus"] == 9 and v["IgnStatus"] == 1
                                    normal &= bool(lamp_frames) and db.decode_message(0x33D, bytes.fromhex(lamp_frames[-1]["data"]), decode_choices=False)["EPS_WarningLampSt"] == 0
                                    normal &= bool(engine_frames) and db.decode_message(0x53, bytes.fromhex(engine_frames[-1]["data"]), decode_choices=False)["VCU_Engine_Running"] == 1
                                result["recovery"] = "BASELINE_VERIFIED" if normal else "BASELINE_NOT_VERIFIED"
                            if args.inject:
                                from recar.smoke import run
                                run(xcp, app, monitor, parameters, frames, out, result, daq=daq, case=case)
                        finally:
                            if daq:
                                daq.stop()
                                result["daq_sample_count"] = len(daq.rows)
                                result["daq_error"] = daq.error
                            if not result.get("xcp_disconnected_before_reset"):
                                xcp.disconnect()
            finally:
                stop.set()
                thread.join(2)
                result["capture_errors"] = errors
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result['error_classification'] = 'INFRASTRUCTURE_ERROR'
    finally:
        if args.daq:
            result["method"] = "10MS_DAQ_OBSERVATION_WITH_SCALAR_READBACK"
        if args.daq and result.get("injection_start_monotonic"):
            try:
                from recar.evidence_report import build
                result["evidence"] = build(out, result)
            except Exception as exc:
                result["report_error"] = str(exc)
        if args.inject:
            from recar.batch import classify
            result['functional_execution'] = classify(result)
            if not result.get('evidence'):
                from recar.evidence_report import build_unavailable
                build_unavailable(out, result)
        (out / "result.json").write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
        print(json.dumps(result, default=str, indent=2))
        print(out)


if __name__ == "__main__":
    main()
