import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from recar.catalog import CATALOG, choices, load_catalog, select_case
from recar.multi_signal import MultiSignalRunner
from recar.offline import run as dry_run


class Client:
    def __init__(self, originals=None, fail_signal=None):
        self.originals = originals or {}
        self.fail_signal = fail_signal
        self.calls = []
    def read(self, parameter):
        value = self.originals.get(parameter.name, 7)
        return SimpleNamespace(raw_value=value, physical_value=value, data=bytes([int(value)]))
    def write_and_verify(self, parameter, value, **kwargs):
        self.calls.append((parameter.name, value))
        actual = value if parameter.name != self.fail_signal else value + 1
        return SimpleNamespace(data=bytes([int(value)])), SimpleNamespace(data=bytes([int(actual)])), actual == value


class MultiSignalTests(unittest.TestCase):
    def runner(self, selection, **client_args):
        case = select_case(selection, 'MULTI_SIGNAL_FIXED')
        parameters = [SimpleNamespace(name=write.signal) for write in case.writes]
        return case, Client(**client_args), parameters

    def test_catalog_contains_exactly_the_44_normal_multi_cases(self):
        cases = choices('MULTI_SIGNAL_FIXED')
        self.assertEqual(len(cases), 44)
        self.assertEqual({case.excel_row for case in cases}, {138, *range(139, 152), *range(153, 156), *range(169, 193), 224, 225, 226})
        self.assertNotIn(215, {case.excel_row for case in cases})

    def test_representative_predriver_a4412_and_motor_mappings(self):
        expected = {
            1138: [('FAULT_INJECT.Predriver_Fault', 1), ('FAULT_INJECT.Predriver_Fault_Test', 1)],
            1169: [('FAULT_INJECT.A4412_Fault', 1), ('FAULT_INJECT.A4412_Fault_Test', 1)],
            1225: [('Motor_Connect_Test', 1), ('mspd_test', 299), ('lq_test', 4.9), ('Vq_test', 16.1)],
        }
        for selection, writes in expected.items():
            case = select_case(selection, 'MULTI_SIGNAL_FIXED')
            self.assertEqual([(write.signal, write.value) for write in case.writes], writes)

    def test_order_capture_readback_and_reverse_restore(self):
        case, client, parameters = self.runner(1224, originals={'Motor_Over_Current_Bus_Test': 4, 'Motor_CurrentBus_test': 5, 'Motor_Speed_test': 6})
        evidence = {}
        MultiSignalRunner(client, parameters, case).inject_and_restore(lambda: None, evidence=evidence)
        writes = [(write.signal, write.value) for write in case.writes]
        self.assertEqual(client.calls[:3], writes)
        self.assertEqual(client.calls[3:], [('Motor_Speed_test', 6), ('Motor_CurrentBus_test', 5), ('Motor_Over_Current_Bus_Test', 4)])
        self.assertEqual([row['order'] for row in evidence['writes']], [1, 2, 3])

    def test_failed_write_stops_later_writes_and_restores_attempted(self):
        case, client, parameters = self.runner(1138, fail_signal='FAULT_INJECT.Predriver_Fault_Test')
        with self.assertRaisesRegex(RuntimeError, 'Predriver_Fault_Test'):
            MultiSignalRunner(client, parameters, case).inject_and_restore(lambda: None)
        self.assertEqual(client.calls[:2], [('FAULT_INJECT.Predriver_Fault', 1), ('FAULT_INJECT.Predriver_Fault_Test', 1)])
        self.assertEqual(client.calls[2:], [('FAULT_INJECT.Predriver_Fault_Test', 7), ('FAULT_INJECT.Predriver_Fault', 7)])

    def test_dry_run_preserves_common_report_metadata(self):
        report = dry_run(select_case(1226, 'MULTI_SIGNAL_FIXED'))
        result = json.loads((report / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['hardware_execution'], 'NOT_RUN')
        self.assertEqual(result['hardware_validation'], 'HARDWARE_VALIDATION_PENDING')
        self.assertEqual(len(result['test_case']['writes']), 3)
        self.assertIn('Hardware validation: PENDING', (report / 'report.html').read_text(encoding='utf-8'))

    def test_missing_or_duplicate_writes_are_rejected(self):
        original = json.loads(CATALOG.read_text(encoding='utf-8'))
        for mutation in ('missing', 'duplicate'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                data = json.loads(json.dumps(original))
                multi = next(case for case in data['cases'] if case['family'] == 'MULTI_SIGNAL_FIXED')
                if mutation == 'missing':
                    multi['writes'] = []
                else:
                    multi['writes'][1]['signal'] = multi['writes'][0]['signal']
                path = Path(tmp) / 'catalog.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_catalog(path)

    def test_repeated_signal_captures_once_writes_in_order_and_restores_once(self):
        writes = (SimpleNamespace(signal='X', value=1, order=1), SimpleNamespace(signal='X', value=3, order=2))
        case = SimpleNamespace(writes=writes)
        client = Client(originals={'X': 9})
        evidence = {}
        MultiSignalRunner(client, [SimpleNamespace(name='X')], case).inject_and_restore(lambda: None, evidence=evidence)
        self.assertEqual(client.calls, [('X', 1), ('X', 3), ('X', 9)])
        self.assertEqual(evidence['originals'], [{'signal': 'X', 'raw': 9, 'physical': 9, 'data': '09', 'order': 1}])
        self.assertEqual(len(evidence['restoration']), 1)
        self.assertTrue(evidence['restoration'][0]['verified'])

    def test_repeated_second_write_failure_restores_pretest_original_once(self):
        writes = (SimpleNamespace(signal='X', value=1, order=1), SimpleNamespace(signal='X', value=3, order=2))
        case = SimpleNamespace(writes=writes)
        class SecondWriteClient(Client):
            def write_and_verify(self, parameter, value, **kwargs):
                self.calls.append((parameter.name, value))
                actual = value + 1 if len(self.calls) == 2 else value
                return SimpleNamespace(data=bytes([int(value)])), SimpleNamespace(data=bytes([int(actual)])), actual == value
        client = SecondWriteClient(originals={'X': 9})
        with self.assertRaisesRegex(RuntimeError, 'X'):
            MultiSignalRunner(client, [SimpleNamespace(name='X')], case).inject_and_restore(lambda: None)
        self.assertEqual(client.calls, [('X', 1), ('X', 3), ('X', 9)])

    def test_row215_uses_confirmed_greater_than_rule_as_ordered_data(self):
        case = select_case(1215, 'SPECIAL_SEQUENCE')
        self.assertEqual(case.excel_row, 215)
        self.assertEqual([(write.signal, write.value, write.order) for write in case.writes],
                         [('PDC_Fault_Pemt_Test', 1, 1), ('PDC_Fault_Pemt_Test', 3, 2)])
        self.assertIn('>x executes as x+1', case.notes)
