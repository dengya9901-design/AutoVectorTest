import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from recar.catalog import CATALOG, choices, families, load_catalog, select_case
from recar.offline import run as dry_run


class CatalogTests(unittest.TestCase):
    def test_family_counts_and_required_mapping(self):
        self.assertEqual(families(), ('MAGCHIP', 'MCU', 'MCU_OS', 'MULTI_SIGNAL_FIXED', 'SENT', 'SPECIAL_SEQUENCE'))
        self.assertEqual({family: len(choices(family)) for family in families()},
                         {'SENT': 16, 'MCU': 30, 'MCU_OS': 38, 'MAGCHIP': 10, 'MULTI_SIGNAL_FIXED': 44, 'SPECIAL_SEQUENCE': 1})
        sent = select_case(1, 'SENT')
        mcu = select_case(37, 'MCU')
        os_case = select_case(99, 'MCU_OS')
        magchip = select_case(228, 'MAGCHIP')
        self.assertEqual((sent.injection_signal, sent.injection_value, sent.fhti_ms, sent.fdti_ms),
                         ('FAULT_INJECT.Sent_Fault_Test', 1, 24, 20))
        self.assertEqual((mcu.injection_signal, mcu.injection_value, mcu.fhti_ms, mcu.fdti_ms),
                         ('FAULT_INJECT.MCU_Fault_Test', 1, 20, 16))
        self.assertEqual((os_case.injection_signal, os_case.injection_value),
                         ('FAULT_INJECT.MCU_OS_Test', 1))
        self.assertEqual((magchip.injection_signal, magchip.injection_value),
                         ('FAULT_INJECT.Magchip_Fault_Test', 1))

    def test_every_case_is_implemented_offline_verified_and_pending_or_tested(self):
        for case in choices():
            with self.subTest(case=case.selection_id):
                self.assertEqual(case.implementation_status, 'IMPLEMENTED')
                self.assertEqual(case.offline_validation_status, 'OFFLINE_VERIFIED')
                self.assertIn(case.hardware_validation_status,
                              ('HARDWARE_VALIDATED', 'HARDWARE_VALIDATION_PENDING'))

    def test_duplicate_missing_and_unsupported_definitions_are_rejected(self):
        original = json.loads(CATALOG.read_text(encoding='utf-8'))
        cases = {
            'duplicate': lambda data: data['cases'].append(data['cases'][0]),
            'missing_field': lambda data: data['cases'][0].pop('expected_fault'),
            'unsupported_signal': lambda data: data['cases'][0].update(injection_signal='FAULT_INJECT.Vbus_Fault_Test'),
            'wrong_family': lambda data: data['cases'][0].update(family='Vbus'),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                data = json.loads(json.dumps(original))
                mutate(data)
                path = Path(tmp) / 'catalog.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises((ValueError, TypeError)):
                    load_catalog(path)

    def test_invalid_or_cross_family_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            select_case(999, 'SENT')
        with self.assertRaises(ValueError):
            select_case(37, 'SENT')

    def test_dry_run_report_is_explicitly_not_hardware_execution(self):
        report_dir = dry_run(select_case(99, 'MCU_OS'))
        self.addCleanup(lambda: None)
        result = json.loads((report_dir / 'result.json').read_text(encoding='utf-8'))
        html = (report_dir / 'report.html').read_text(encoding='utf-8')
        self.assertEqual(result['hardware_execution'], 'NOT_RUN')
        self.assertIn('Hardware execution: NOT_RUN', html)
        self.assertIn('This report is not a PASS report.', html)

    def test_each_family_dry_run_preserves_case_report_metadata(self):
        representatives = {'SENT': 1, 'MCU': 37, 'MCU_OS': 99, 'MAGCHIP': 228}
        for family, selection in representatives.items():
            with self.subTest(family=family):
                case = select_case(selection, family)
                result = json.loads((dry_run(case) / 'result.json').read_text(encoding='utf-8'))
                metadata = result['test_case']
                self.assertEqual((metadata['family'], metadata['injection_signal'], metadata['injection_value']),
                                 (family, case.injection_signal, case.injection_value))
                self.assertEqual((metadata['fhti_ms'], metadata['fdti_ms']),
                                 (case.fhti_ms, case.fdti_ms))
                self.assertEqual(result['hardware_execution'], 'NOT_RUN')

    def test_common_runner_routing_only_on_explicit_hardware_flag(self):
        from recar.run import main
        case = select_case(228, 'MAGCHIP')
        with patch('sys.argv', ['run', '--family', 'MAGCHIP', '--case', '228', '--execute-hardware']), \
             patch('recar.probe.main') as execute, patch('builtins.print'):
            main()
            execute.assert_called_once_with(['--case', str(case.selection_id), '--connect', '--inject'])

    def test_invalid_cli_selection_exits_before_routing(self):
        completed = subprocess.run([sys.executable, '-B', '-m', 'recar.run', '--family', 'SENT', '--case', '999'],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('Unknown or disabled test selection', completed.stderr)
