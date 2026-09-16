from __future__ import annotations

import time
import struct
from dataclasses import dataclass

from recar.parameter import CalibrationParameter


@dataclass(frozen=True)
class CalibrationValue:
    parameter: CalibrationParameter
    physical_value: float
    raw_value: int | float
    data: bytes

    @property
    def hex_data(self) -> str:
        return self.data.hex(" ")


@dataclass(frozen=True)
class CalibrationWriteStep:
    physical_value: float
    hold_sec: float
    note: str = ""


def _struct_format(parameter: CalibrationParameter) -> str:
    endian = {
        "MSB_LAST": "<",
        "LITTLE_ENDIAN": "<",
        "MSB_FIRST": ">",
        "BIG_ENDIAN": ">",
    }.get(parameter.byte_order)
    if endian is None:
        raise ValueError(f"Unsupported calibration byte_order: {parameter.byte_order}")

    data_format = {
        "UBYTE": "B",
        "SBYTE": "b",
        "UWORD": "H",
        "SWORD": "h",
        "ULONG": "I",
        "SLONG": "i",
        "A_UINT64": "Q",
        "A_INT64": "q",
        "FLOAT32_IEEE": "f",
        "FLOAT64_IEEE": "d",
    }.get(parameter.data_type)
    if data_format is None:
        raise ValueError(f"Unsupported calibration data_type: {parameter.data_type}")
    return endian + data_format


def raw_to_physical(parameter: CalibrationParameter, raw_value: int | float) -> float:
    return raw_value * parameter.phys_gain + parameter.phys_offset


def physical_to_raw(parameter: CalibrationParameter, physical_value: float) -> int | float:
    if not (parameter.phys_min <= physical_value <= parameter.phys_max):
        raise ValueError(
            f"{parameter.name} {parameter.role} value {physical_value} {parameter.phys_unit} "
            f"is outside [{parameter.phys_min}, {parameter.phys_max}]"
        )

    converted = (physical_value - parameter.phys_offset) / parameter.phys_gain
    if parameter.data_type in {"FLOAT32_IEEE", "FLOAT64_IEEE"}:
        raw_value: int | float = float(converted)
    else:
        raw_value = round(converted)

    integer_ranges = {
        "UBYTE": (0, 255),
        "SBYTE": (-128, 127),
        "UWORD": (0, 65535),
        "SWORD": (-32768, 32767),
        "ULONG": (0, 4294967295),
        "SLONG": (-2147483648, 2147483647),
        "A_UINT64": (0, 18446744073709551615),
        "A_INT64": (-9223372036854775808, 9223372036854775807),
    }
    limits = integer_ranges.get(parameter.data_type)
    if limits is not None and not (limits[0] <= raw_value <= limits[1]):
        raise ValueError(f"Raw value {raw_value} does not fit {parameter.data_type}")

    return raw_value


def pack_raw(parameter: CalibrationParameter, raw_value: int | float) -> bytes:
    return struct.pack(_struct_format(parameter), raw_value)


def unpack_raw(parameter: CalibrationParameter, data: bytes) -> int:
    expected_size = struct.calcsize(_struct_format(parameter))
    if len(data) < expected_size:
        raise ValueError(f"Expected at least {expected_size} bytes, got {len(data)}")
    return struct.unpack(_struct_format(parameter), data[:expected_size])[0]


def build_calibration_value(
    parameter: CalibrationParameter,
    physical_value: float,
) -> CalibrationValue:
    raw_value = physical_to_raw(parameter, physical_value)
    data = pack_raw(parameter, raw_value)
    return CalibrationValue(
        parameter=parameter,
        physical_value=physical_value,
        raw_value=raw_value,
        data=data,
    )


def decode_calibration_value(
    parameter: CalibrationParameter,
    data: bytes,
) -> CalibrationValue:
    raw_value = unpack_raw(parameter, data)
    physical_value = raw_to_physical(parameter, raw_value)
    return CalibrationValue(
        parameter=parameter,
        physical_value=physical_value,
        raw_value=raw_value,
        data=data[: struct.calcsize(_struct_format(parameter))],
    )


class XcpCalibrationClient:
    def __init__(self, xcp_master) -> None:
        self.xcp = xcp_master

    def read(self, parameter: CalibrationParameter) -> CalibrationValue:
        self.xcp.setMta(parameter.address, parameter.address_ext)
        data = self.xcp.upload(struct.calcsize(_struct_format(parameter)))
        return decode_calibration_value(parameter, data)

    def write(self, parameter: CalibrationParameter, physical_value: float) -> CalibrationValue:
        value = build_calibration_value(parameter, physical_value)
        self.xcp.setMta(parameter.address, parameter.address_ext)
        self.xcp.download(value.data)
        return value

    def write_and_verify(
        self,
        parameter: CalibrationParameter,
        physical_value: float,
        *,
        tolerance: float | None = None,
    ) -> tuple[CalibrationValue, CalibrationValue, bool]:
        expected = self.write(parameter, physical_value)
        actual = self.read(parameter)
        # The requested physical value can be quantized when it is encoded into
        # an integer A2L object.  The ECU write is verified by the encoded raw
        # representation, rather than by comparing an unquantized request with
        # a decoded physical readback.  This is deterministic for all scalar
        # parameter types and avoids arbitrary per-case tolerances.
        ok = actual.raw_value == expected.raw_value
        return expected, actual, ok


def write_timed_sequence(
    client: XcpCalibrationClient,
    parameter: CalibrationParameter,
    steps: list[CalibrationWriteStep],
) -> list[tuple[CalibrationWriteStep, CalibrationValue, float]]:
    results: list[tuple[CalibrationWriteStep, CalibrationValue, float]] = []
    for step in steps:
        started = time.perf_counter()
        written = client.write(parameter, step.physical_value)
        elapsed = time.perf_counter() - started
        remaining = step.hold_sec - elapsed
        if remaining > 0:
            time.sleep(remaining)
        results.append((step, written, elapsed))
    return results
