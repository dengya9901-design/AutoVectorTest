"""Offline catalog dry-run: validates routing without contacting the ECU."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from recar.catalog import TestCase

ROOT = Path(__file__).resolve().parents[1]


def run(case: TestCase) -> Path:
    out = ROOT / 'reports' / 'dry_run' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    result = {
        'test_case': case.metadata(), 'method': 'OFFLINE_CATALOG_DRY_RUN',
        'hardware_execution': 'NOT_RUN', 'hardware_validation': case.hardware_validation_status,
        'implementation': case.implementation_status,
        'offline_validation': case.offline_validation_status,
        'recovery_validation': case.recovery_validation_status,
        'injection': 'NOT_RUN', 'timing': 'NOT_EVALUATED', 'dtc': 'NOT_IMPLEMENTED',
        'routing': 'TestCase -> catalog-selected runner -> A2L/XCP/DAQ/recovery/report',
    }
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    c = case.metadata()
    write_summary = ', '.join(f"{write['order']}: {write['signal']} = {write['value']}" for write in c['writes']) if c['writes'] else f"{c['injection_signal']} = {c['injection_value']}"
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{html.escape(c['family'])} dry run</title>
<style>body{{font:15px system-ui;max-width:1000px;margin:32px auto;padding:0 24px}}table{{border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}.pending{{color:#9a5c00}}</style>
<h1>Offline catalog dry run</h1><p class="pending">Hardware execution: NOT_RUN<br>Hardware validation: PENDING ({html.escape(c['hardware_validation_status'])})</p>
<table><tr><th>Family</th><td>{html.escape(c['family'])}</td></tr><tr><th>Selection</th><td>{c['selection_id']}</td></tr><tr><th>Excel row</th><td>{c['excel_row']}</td></tr><tr><th>TSR</th><td>{html.escape(c['tsr_id'])}</td></tr><tr><th>Fault</th><td>{html.escape(c['expected_fault'])}</td></tr><tr><th>Writes</th><td>{html.escape(write_summary)}</td></tr><tr><th>FHTI / FDTI</th><td>{c['fhti_ms']} ms / {c['fdti_ms']} ms</td></tr><tr><th>Implementation</th><td>{html.escape(c['implementation_status'])}</td></tr><tr><th>Offline validation</th><td>{html.escape(c['offline_validation_status'])}</td></tr><tr><th>Recovery</th><td>{html.escape(c['recovery_validation_status'])}</td></tr></table>
<p>No ECU, XCP, CAN, DAQ, UDS, fault injection, or timing measurement was performed. This report is not a PASS report.</p></html>'''
    (out / 'report.html').write_text(report, encoding='utf-8')
    return out
