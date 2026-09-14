import unittest
from types import SimpleNamespace
from recar.smoke import inject_and_restore
from recar.catalog import choices

class Client:
    def __init__(self, mismatch=False):
        self.values = []
        self.mismatch = mismatch
    def read(self, p):
        return SimpleNamespace(raw_value=0, physical_value=0, data=b'\0')
    def write_and_verify(self, p, value, **kwargs):
        self.values.append(value)
        v = SimpleNamespace(data=bytes([value]))
        return v, v, not (value == 1 and self.mismatch)

class RestoreTests(unittest.TestCase):
    def test_every_catalog_case_uses_same_restore_flow(self):
        for case in (case for case in choices() if not case.is_multi_signal):
            with self.subTest(selection=case.selection_id):
                c = Client()
                inject_and_restore(c, SimpleNamespace(name=case.injection_signal), lambda: None, case=case)
                self.assertEqual(c.values, [case.injection_value, 0])
    def test_selected_golden_restores(self):
        c = Client()
        inject_and_restore(c, None, lambda: None)
        self.assertEqual(c.values, [1, 0])
    def test_invalid_value_does_not_write(self):
        c = Client()
        with self.assertRaises(ValueError):
            inject_and_restore(c, None, lambda: None, fault_value=17)
        self.assertEqual(c.values, [])
    def test_observer_exception_restores(self):
        c = Client()
        with self.assertRaises(ValueError):
            inject_and_restore(c, None, lambda: (_ for _ in ()).throw(ValueError('acquisition failed')))
        self.assertEqual(c.values, [1, 0])
    def test_injection_mismatch_restores(self):
        c = Client(True)
        with self.assertRaises(RuntimeError):
            inject_and_restore(c, None, lambda: None)
        self.assertEqual(c.values, [1, 0])
