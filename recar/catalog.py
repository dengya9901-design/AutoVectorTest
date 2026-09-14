"""Data-only test definitions and validated formal selection."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path

CATALOG = Path(__file__).with_name('catalog.json')


@dataclass(frozen=True)
class CalibrationWrite:
    signal: str
    value: int | float
    order: int


@dataclass(frozen=True)
class TestCase:
    family: str
    selection_id: int
    excel_row: int
    tsr_id: str
    injection_signal: str | None
    injection_value: int | float | None
    expected_fault: str
    fhti_ms: float
    fdti_ms: float
    enabled: bool
    description: str
    source_cells: dict
    implementation_status: str
    offline_validation_status: str
    hardware_validation_status: str
    hardware_execution_status: str
    recovery_validation_status: str
    notes: str = ''
    writes: tuple[CalibrationWrite, ...] = ()
    source_step: str = ''

    def metadata(self):
        return asdict(self)

    @property
    def is_multi_signal(self):
        return bool(self.writes)


def load_catalog(path=CATALOG):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported catalog version')
    cases = [TestCase(**{**row, 'writes': tuple(CalibrationWrite(**write) for write in row.get('writes', ()))}) for row in data['cases']]
    ids = [case.selection_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate selection ID')
    for case in cases:
        valid_single = (type(case.injection_value) is int and 1 <= case.injection_value <= 255
                        and (case.family, case.injection_signal) in {
                    ('SENT', 'FAULT_INJECT.Sent_Fault_Test'),
                    ('MCU', 'FAULT_INJECT.MCU_Fault_Test'),
                    ('MCU_OS', 'FAULT_INJECT.MCU_OS_Test'),
                    ('MAGCHIP', 'FAULT_INJECT.Magchip_Fault_Test')})
        valid_multi = (case.family in {'MULTI_SIGNAL_FIXED', 'SPECIAL_SEQUENCE'} and len(case.writes) >= 2
                       and [write.order for write in case.writes] == list(range(1, len(case.writes) + 1))
                       and (case.family == 'SPECIAL_SEQUENCE' or len({write.signal for write in case.writes}) == len(case.writes))
                       and all(write.signal and isinstance(write.value, (int, float)) for write in case.writes))
        if (type(case.selection_id) is not int or case.selection_id < 1
                or not (valid_single or valid_multi)
                or type(case.enabled) is not bool
                or not case.expected_fault or not case.tsr_id
                or not 0 < case.fdti_ms <= case.fhti_ms):
            raise ValueError(f'Unsupported or invalid test definition: {case.selection_id}')
        if case.injection_signal == 'FAULT_INJECT.Sent_Fault_Test' and case.injection_value > 16:
            raise ValueError('Sent value is outside the validated catalog scope')
        if case.implementation_status != 'IMPLEMENTED' or case.offline_validation_status != 'OFFLINE_VERIFIED':
            raise ValueError(f'Catalog validation status is incomplete: {case.selection_id}')
        if case.hardware_validation_status not in {'HARDWARE_VALIDATED', 'HARDWARE_VALIDATION_PENDING'}:
            raise ValueError(f'Invalid hardware validation status: {case.selection_id}')
    if not any(c.selection_id == data['default_selection_id'] and c.enabled for c in cases):
        raise ValueError('Default test is not enabled')
    return data, cases


def choices(family=None):
    return [case for case in load_catalog()[1] if case.enabled and (family is None or case.family == family)]


def families():
    return tuple(sorted({case.family for case in choices()}))


def select_case(selection_id=None, family=None):
    data, cases = load_catalog()
    selected = data['default_selection_id'] if selection_id is None else selection_id
    for case in cases:
        if case.selection_id == selected and case.enabled and (family is None or case.family == family):
            return case
    raise ValueError(f'Unknown or disabled test selection: {selected}')
