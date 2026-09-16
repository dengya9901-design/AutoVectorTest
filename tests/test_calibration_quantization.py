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


if __name__ == "__main__":
    unittest.main()
