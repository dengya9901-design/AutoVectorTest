"""Sequential catalog selections; stop after the first incomplete or blocked case."""
import html
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from recar.catalog import choices, select_case
from recar.recovery_policy import may_continue_after_case, recovery_failures

ROOT=Path(__file__).resolve().parents[1]


def failures(result):
    checks={
        'injection_readback': result.get('injection')=='READBACK_VERIFIED',
        'selector_restore': result.get('selector_restore')=='READBACK_VERIFIED',
        'warning': result.get('warning_observed') is True,
        'reset_response': bool(result.get('reset_positive_response')),
        'recovery': result.get('recovery')=='BASELINE_VERIFIED',
        'window': result.get('evidence',{}).get('window_complete') is True,
        'event_pairing': result.get('evidence',{}).get('timing',{}).get('pairing')=='UNIQUE_ORDERED_TRIPLET',
        'daq': result.get('daq_period_ms')==10 and not result.get('daq_error') and result.get('daq_sample_count',0)>0,
        'errors': not any(result.get(k) for k in ('error','report_error','capture_errors')),
    }
    return [name for name,ok in checks.items() if not ok]


def classify(result):
    reasons = failures(result)
    infrastructure = {'injection_readback', 'selector_restore', 'window', 'daq', 'errors'}
    status = ('BLOCKED' if infrastructure.intersection(reasons)
              else 'INCOMPLETE' if reasons else 'COMPLETED')
    return {'status': status, 'reasons': reasons,
            'timing_verdict': 'NOT_EVALUATED', 'dtc': 'NOT_IMPLEMENTED'}


def save(out, entries, state, selected_ids=None):
    definitions = {case.selection_id: case for case in choices()}
    selected_ids = list(definitions) if selected_ids is None else selected_ids
    remaining=[v for v in selected_ids if v not in {e['value'] for e in entries}]
    summary={'status':state,'cases':entries,'selected_ids':selected_ids,'not_executed_values':remaining,'timing_verdict':'NOT_EVALUATED','dtc':'NOT_IMPLEMENTED'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    rows=''
    for e in entries:
        timing=e.get('timing',{})
        fdti=timing.get('11B_minus_11A_ms')
        frti=timing.get('11C_minus_11B_ms')
        link=Path(e['report']).as_uri() if e.get('report') else '#'
        fdtitext=f'{fdti:.3f}' if fdti is not None else '—'
        frtitext=f'{frti:.3f}' if frti is not None else '—'
        detail='; '.join(e.get('failures',[]))
        case = definitions.get(e['value'])
        fault_name = case.expected_fault if case else e.get('expected_fault', 'Legacy test')
        rows+=f'<tr><td>{e["value"]}</td><td>{html.escape(fault_name)}</td><td>{html.escape(e["status"])}<br><small>{html.escape(detail)}</small></td><td>{fdtitext}</td><td>{frtitext}</td><td><a href="{link}">Report</a></td></tr>'
    for value in remaining:
        fault_name = definitions[value].expected_fault if value in definitions else 'Legacy test'
        rows+=f'<tr><td>{value}</td><td>{html.escape(fault_name)}</td><td>NOT EXECUTED</td><td>—</td><td>—</td><td>—</td></tr>'
    (out/'summary.html').write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Recar single-signal batch</title>
<style>body{{font:15px system-ui;margin:40px;color:#234}}table{{border-collapse:collapse}}td,th{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>Recar single-signal batch · 10 ms DAQ</h1><p>{state}</p><table><tr><th>Selection</th><th>Fault</th><th>Functional execution</th><th>FDTI / ms</th><th>FRTI / ms</th><th>Evidence</th></tr>{rows}</table>
<p>Timing limits not evaluated. DTC not implemented. Functional completion is not a full TSR PASS.</p></html>''',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-hardware', action='store_true',
                        help='Required acknowledgement before any ECU batch execution')
    parser.add_argument('--cases', nargs='+', type=int, choices=[c.selection_id for c in choices()],
                        help='Catalog selections, in execution order; default: all enabled cases')
    args = parser.parse_args()
    if not args.execute_hardware:
        parser.error('Hardware batch execution requires --execute-hardware')
    selected_ids = args.cases if args.cases is not None else [c.selection_id for c in choices()]
    if len(selected_ids) != len(set(selected_ids)):
        parser.error('Duplicate selections are not allowed in one batch')
    out=ROOT/'reports/batch'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    entries=[]
    save(out,entries,'RUNNING',selected_ids)
    print('BATCH_REPORT '+str(out),flush=True)
    for index,value in enumerate(selected_ids):
        case = select_case(value)
        print(f'START {value}: {case.expected_fault}',flush=True)
        entry={'value':value,'status':'INCOMPLETE','expected_fault':case.expected_fault,
               'test_case':case.metadata()}
        try:
            log=out/f'fault_{value}.log'
            with log.open('w',encoding='utf-8') as stream:
                process=subprocess.run([sys.executable,'-B','-m','recar.probe','--connect','--inject','--case',str(value)],
                                       cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=150)
            report=Path(log.read_text(encoding='utf-8').strip().splitlines()[-1])
            result=json.loads((report/'result.json').read_text(encoding='utf-8'))
            reasons=failures(result)
            functional_status=result.get('functional_execution',{}).get('status') or classify(result)['status']
            isolation_failures=recovery_failures(result)
            if process.returncode:
                reasons.append('process_exit')
            entry.update(report=str(report/'report.html'),result=str(report/'result.json'),
                         timing=result.get('evidence',{}).get('timing',{}),failures=reasons,
                         recovery_failures=isolation_failures,
                         status='BLOCKED' if process.returncode else functional_status)
        except Exception as exc:
            entry['error']=str(exc)
            entry['status']='BLOCKED'
        entries.append(entry)
        print(json.dumps(entry),flush=True)
        if entry['status']=='BLOCKED' or not may_continue_after_case(entry['status'], result):
            save(out,entries,'STOPPED_ON_RECOVERY_OR_INFRASTRUCTURE_FAILURE',selected_ids)
            print('STOPPED '+str(out),flush=True)
            return
        save(out,entries,'RUNNING' if index<len(selected_ids)-1 else 'COMPLETED',selected_ids)
    print('COMPLETED '+str(out),flush=True)


if __name__=='__main__':
    main()
