"""Append normal fixed multi-write TSR records to the existing catalog."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STEP = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(?:\([^)]*\))?\s*(=|>|为|置)\s*(-?\d+(?:\.\d+)?)")


def number(text):
    value = float(text)
    return int(value) if value.is_integer() else value


def source_value(operator, text):
    value = number(text)
    return value + 1 if operator == '>' else value


def main():
    catalog_path = ROOT / 'recar' / 'catalog.json'
    source_path = ROOT / 'recarTest' / 'remaining_hy_classification.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    source = json.loads(source_path.read_text(encoding='utf-8'))
    catalog['cases'] = [case for case in catalog['cases'] if case['family'] not in {'MULTI_SIGNAL_FIXED', 'SPECIAL_SEQUENCE'}]
    for record in source['records']:
        if record['category'] != 'MULTI_SIGNAL_FIXED':
            continue
        matches = STEP.findall(record['g_column_test_step'])
        if len(matches) < 2 or len({signal for signal, _, _ in matches}) != len(matches):
            raise ValueError(f"Excel row {record['excel_row']} is not a normal unique multi-write payload")
        writes = [{'signal': signal, 'value': source_value(operator, value), 'order': index}
                  for index, (signal, operator, value) in enumerate(matches, 1)]
        catalog['cases'].append({
            'family': 'MULTI_SIGNAL_FIXED', 'selection_id': 1000 + record['excel_row'],
            'excel_row': record['excel_row'], 'tsr_id': record['tsr_id'],
            'injection_signal': None, 'injection_value': None,
            'expected_fault': record['fault_name'], 'fhti_ms': record['fhti_ms'], 'fdti_ms': record['fdti_ms'],
            'enabled': True, 'description': 'Fixed ordered multi-signal software injection',
            'source_cells': {'fault_name': f"E{record['excel_row']}", 'injection': f"G{record['excel_row']}",
                             'enabled': f"H{record['excel_row']}"},
            'implementation_status': 'IMPLEMENTED', 'offline_validation_status': 'OFFLINE_VERIFIED',
            'hardware_validation_status': 'HARDWARE_VALIDATION_PENDING', 'hardware_execution_status': 'NOT_RUN',
            'recovery_validation_status': 'HARDWARE_VALIDATION_REQUIRED',
            'notes': 'Catalog data only. Restoring parameters is not DTC clearing.',
            'writes': writes, 'source_step': record['g_column_test_step'],
        })
    row215 = next(record for record in source['records'] if record['excel_row'] == 215)
    sequence = STEP.findall(row215['g_column_test_step'].translate(str.maketrans({'＝': '=', '＞': '>', '（': '(', '）': ')'})))
    if len(sequence) != 2 or sequence[0][0] != sequence[1][0] or sequence[1][1] != '>':
        raise ValueError('Row 215 source sequence is not the confirmed repeated >x form')
    catalog['cases'].append({
        'family': 'SPECIAL_SEQUENCE', 'selection_id': 1215, 'excel_row': 215, 'tsr_id': row215['tsr_id'],
        'injection_signal': None, 'injection_value': None, 'expected_fault': row215['fault_name'],
        'fhti_ms': row215['fhti_ms'], 'fdti_ms': row215['fdti_ms'], 'enabled': True,
        'description': 'Ordered repeated-signal software injection',
        'source_cells': {'fault_name': 'E215', 'injection': 'G215', 'enabled': 'H215'},
        'implementation_status': 'IMPLEMENTED', 'offline_validation_status': 'OFFLINE_VERIFIED',
        'hardware_validation_status': 'HARDWARE_VALIDATION_PENDING', 'hardware_execution_status': 'NOT_RUN',
        'recovery_validation_status': 'HARDWARE_VALIDATION_REQUIRED',
        'notes': 'Source rule confirmed by engineering: >x executes as x+1. Restoring parameters is not DTC clearing.',
        'writes': [{'signal': signal, 'value': source_value(operator, value), 'order': index}
                   for index, (signal, operator, value) in enumerate(sequence, 1)],
        'source_step': row215['g_column_test_step'],
    })
    ids = [case['selection_id'] for case in catalog['cases']]
    if len(ids) != len(set(ids)) or sum(c['family'] == 'MULTI_SIGNAL_FIXED' for c in catalog['cases']) != 44:
        raise ValueError('Multi-signal catalog integrity failure')
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
