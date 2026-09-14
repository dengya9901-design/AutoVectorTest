"""Data-driven ordered XCP write and restoration for normal multi-signal cases."""
from __future__ import annotations

import time


class MultiSignalRunner:
    def __init__(self, client, parameters, case):
        self.client = client
        self.case = case
        self.writes = tuple(sorted(case.writes, key=lambda write: write.order))
        by_name = {parameter.name: parameter for parameter in parameters}
        if set(by_name) != {write.signal for write in self.writes}:
            raise ValueError("Resolved parameters do not match the selected writes")
        self.parameters = by_name

    def inject_and_restore(self, observe, before_write=None, evidence=None):
        originals = {}
        attempted_signals = []
        for write in self.writes:
            if write.signal in originals:
                continue
            original = self.client.read(self.parameters[write.signal])
            originals[write.signal] = original
            if evidence is not None:
                evidence.setdefault("originals", []).append({"signal": write.signal, "raw": original.raw_value,
                    "physical": original.physical_value, "data": original.data.hex(), "order": write.order})
        try:
            if before_write:
                before_write()
            for write in self.writes:
                parameter = self.parameters[write.signal]
                if write.signal not in attempted_signals:
                    attempted_signals.append(write.signal)
                expected, actual, ok = self.client.write_and_verify(parameter, write.value, tolerance=0)
                verified = bool(ok and actual.data == expected.data)
                if evidence is not None:
                    evidence.setdefault("writes", []).append({"signal": write.signal, "value": write.value,
                        "order": write.order, "expected_data": expected.data.hex(),
                        "readback_data": actual.data.hex(), "verified": verified, "monotonic": time.monotonic()})
                if not verified:
                    raise RuntimeError(f"Injection readback mismatch: {write.signal}")
            observe()
        finally:
            restoration = []
            restore_failures = []
            for signal in reversed(attempted_signals):
                parameter, original = self.parameters[signal], originals[signal]
                expected, actual, ok = self.client.write_and_verify(parameter, original.physical_value, tolerance=0)
                verified = bool(ok and actual.data == original.data)
                restoration.append({"signal": signal, "expected_data": original.data.hex(),
                    "readback_data": actual.data.hex(), "verified": verified, "monotonic": time.monotonic()})
                if not verified:
                    restore_failures.append(signal)
            if evidence is not None:
                evidence["restoration"] = restoration
            if restore_failures:
                raise RuntimeError("RESTORE_FAILED: " + ", ".join(restore_failures) + " requires operator attention")
