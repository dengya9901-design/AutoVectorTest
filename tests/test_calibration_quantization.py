import struct
import unittest

from recar.calibration import XcpCalibrationClient
from recar.parameter import CalibrationParameter


class FakeXcp:
    def __init__(self):
        self.data = b"\x00\x00"

    def setMta(self, address, address_ext):
        self.address = (address, address_ext)

    def download(self, data):
        self.data = data

    def upload(self, length):
        return self.data[:length]


def gain128_parameter():
    return CalibrationParameter(
        name="Gain128_Test", symbol_link="Gain128_Test", role="test", side="",
        address=0x1000, address_ext=0, data_type="SWORD", byte_order="MSB_LAST",
        conversion_name="gain128", conversion_type="LINEAR", phys_gain=1 / 128,
        phys_offset=0.0, phys_unit="MotorToq", phys_min=-256.0, phys_max=256.0,
        source_file="test",
    )


def gain1024_parameter():
    return CalibrationParameter(
        name="Gain1024_Test", symbol_link="Gain1024_Test", role="test", side="",
        address=0x1002, address_ext=0, data_type="SWORD", byte_order="MSB_LAST",
        conversion_name="gain1024_4", conversion_type="LINEAR", phys_gain=1 / 1024,
        phys_offset=0.0, phys_unit="MotorTorq", phys_min=-32.0, phys_max=32.0,
        source_file="test",
    )


def float32_parameter():
    return CalibrationParameter(
        name="Float32_Test", symbol_link="Float32_Test", role="test", side="",
        address=0x1004, address_ext=0, data_type="FLOAT32_IEEE", byte_order="MSB_LAST",
        conversion_name="NO_COMPU_METHOD", conversion_type="NO_COMPU_METHOD", phys_gain=1.0,
        phys_offset=0.0, phys_unit="", phys_min=-1000.0, phys_max=1000.0, source_file="test",
    )


def gain819_parameter():
    return CalibrationParameter(
        name="Gain819_Test", symbol_link="Gain819_Test", role="test", side="",
        address=0x1008, address_ext=0, data_type="UWORD", byte_order="MSB_LAST",
        conversion_name="gain_819", conversion_type="LINEAR", phys_gain=1 / 819,
        phys_offset=0.0, phys_unit="V", phys_min=0.0, phys_max=80.0, source_file="test",
    )


class CalibrationQuantizationTests(unittest.TestCase):
    def test_gain128_readback_is_verified_by_encoded_raw_value(self):
        client = XcpCalibrationClient(FakeXcp())
        expected, actual, verified = client.write_and_verify(
            gain128_parameter(), 11.1, tolerance=0
        )
        self.assertEqual(expected.raw_value, 1421)
        self.assertEqual(actual.raw_value, 1421)
        self.assertEqual(expected.data, struct.pack("<h", 1421))
        self.assertEqual(actual.physical_value, 1421 / 128)
        self.assertNotEqual(actual.physical_value, 11.1)
        self.assertTrue(verified)

    def test_gain128_exact_value_has_expected_raw_encoding(self):
        client = XcpCalibrationClient(FakeXcp())
        expected, actual, verified = client.write_and_verify(
            gain128_parameter(), 3.0, tolerance=0
        )
        self.assertEqual((expected.raw_value, actual.raw_value), (384, 384))
        self.assertTrue(verified)

    def test_raw_readback_mismatch_is_rejected(self):
        xcp = FakeXcp()
        client = XcpCalibrationClient(xcp)
        original_download = xcp.download

        def altered_download(data):
            original_download(struct.pack("<h", 1))

        xcp.download = altered_download
        _, _, verified = client.write_and_verify(gain128_parameter(), 3.0, tolerance=0)
        self.assertFalse(verified)

    def test_gain1024_offsets_use_encoded_raw_readback(self):
        for requested, expected_raw in ((-6.9, -7066), (6.9, 7066)):
            with self.subTest(requested=requested):
                expected, actual, verified = XcpCalibrationClient(FakeXcp()).write_and_verify(
                    gain1024_parameter(), requested, tolerance=0
                )
                self.assertEqual((expected.raw_value, actual.raw_value), (expected_raw, expected_raw))
                self.assertEqual(actual.physical_value, expected_raw / 1024)
                self.assertTrue(verified)

    def test_float32_readback_uses_encoded_bytes_not_python_float_equality(self):
        expected, actual, verified = XcpCalibrationClient(FakeXcp()).write_and_verify(
            float32_parameter(), 4.9, tolerance=0
        )
        self.assertEqual(expected.data, actual.data)
        self.assertNotEqual(expected.raw_value, actual.raw_value)
        self.assertTrue(verified)

    def test_gain819_temperature_variants_use_encoded_raw_readback(self):
        for requested, expected_raw in ((4.96, 4062), (0.03, 25)):
            with self.subTest(requested=requested):
                expected, actual, verified = XcpCalibrationClient(FakeXcp()).write_and_verify(
                    gain819_parameter(), requested, tolerance=0
                )
                self.assertEqual((expected.raw_value, actual.raw_value), (expected_raw, expected_raw))
                self.assertTrue(verified)


if __name__ == "__main__":
    unittest.main()
