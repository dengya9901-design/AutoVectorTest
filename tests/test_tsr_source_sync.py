import json
from pathlib import Path
import unittest

from recar.batch import resolve_named_batch
from recar.catalog import CATALOG, choices, select_case


SYNC = Path(__file__).resolve().parents[1] / 'TSR_SOURCE_SYNC_20260915.json'


class LatestTsrSourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
        self.sync = json.loads(SYNC.read_text(encoding='utf-8'))

    def test_removed_rows_are_traceable_and_never_selectable(self):
        removed_rows = {item['excel_row'] for item in self.sync['removed_or_obsolete']}
        self.assertEqual(removed_rows, {216, 223})
        self.assertTrue(all(item['execution_status'] == 'REMOVED_OR_OBSOLETE'
                            for item in self.sync['removed_or_obsolete']))
        self.assertFalse(removed_rows & {case.excel_row for case in choices()})

    def test_removed_rows_do_not_shift_downstream_identity(self):
        rows = {case.excel_row for case in choices('MULTI_SIGNAL_FIXED')}
        self.assertIn(224, rows)
        self.assertIn(225, rows)
        self.assertIn(226, rows)
        self.assertEqual(select_case(1226, 'MULTI_SIGNAL_FIXED').excel_row, 226)

    def test_rows_218_and_219_exclude_obsolete_threshold_write(self):
        for row, offset in ((218, -6.9), (219, 6.9)):
            case = next(item for item in choices('PARAMETER_OFFSET') if item.excel_row == row)
            self.assertEqual([(write.signal, write.value, write.order) for write in case.writes], [
                ('BasicTorque_Switch', 1, 1),
                ('BasicTorque_Offset', offset, 2),
            ])
            self.assertNotIn('AimTorqueCheckThreshold', [write.signal for write in case.writes])

    def test_row_226_excludes_obsolete_speed_write(self):
        case = select_case(1226, 'MULTI_SIGNAL_FIXED')
        self.assertEqual([(write.signal, write.value, write.order) for write in case.writes], [
            ('Motor_Over_Current_Phase_Test', 1, 1),
            ('Motor_CurrentPhase_test', 241, 2),
        ])
        self.assertNotIn('Mspd_test', [write.signal for write in case.writes])
        self.assertEqual(case.offline_validation_status, 'A2L_VALIDATION_REQUIRED')

    def test_mcu_scope_and_row_98_definition_are_unchanged(self):
        cases = choices('MCU')
        self.assertEqual(len(cases), 62)
        row_42 = next(case for case in cases if case.excel_row == 42)
        self.assertEqual((row_42.injection_value, row_42.expected_fault),
                         (6, 'MCU_CORE0_CLKMTST_FAILURE'))
        row_98 = next(case for case in cases if case.excel_row == 98)
        self.assertEqual((row_98.injection_signal, row_98.injection_value, row_98.expected_fault),
                         ('FAULT_INJECT.MCU_Fault_Test', 62, 'MCU_GTM_MODULE_FAILURE'))
        self.assertEqual(self.sync['existing_hardware_evidence']['row_98']['classification'], 'INCOMPLETE')

    def test_explicit_batch_scope_does_not_depend_on_h_column(self):
        self.assertIn('not an execution input', self.sync['selection_policy'])
        self.assertEqual([case.injection_value for case in resolve_named_batch('sent-all')], list(range(1, 17)))
        self.assertEqual(len(choices()), 187)
