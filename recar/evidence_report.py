"""English evidence report; A/B/C semantics confirmed by the user."""
import csv
import html
import json
import math


def display(value, unavailable='N/A'):
    """Return safe display text without inventing absent catalog metadata."""
    return unavailable if value is None else str(value)


def escape(value, unavailable='N/A'):
    return html.escape(display(value, unavailable))


def case_title(case):
    value = case.get('injection_value')
    suffix = f'Fault {value}' if value is not None else 'Ordered injection'
    return f'{display(case.get("tsr_id"))} · {suffix}'


def ordered_write_metadata(case, calibration):
    """Render catalog order and hardware evidence for any ordered-write case."""
    writes = sorted(case.get('writes') or [], key=lambda row: row.get('order', 0))
    if not writes:
        signal = case.get('injection_signal')
        value = case.get('injection_value')
        return (f'<p class="caption">{escape(signal)} = {escape(value)}</p>', '')

    catalog_rows = ''.join(
        f'<tr><td>{escape(row.get("order"))}</td><td>{escape(row.get("signal"))}</td>'
        f'<td>{escape(row.get("value"))}</td></tr>' for row in writes)
    catalog = ('<h2>Ordered injection definition</h2><table><tr><th>Order</th><th>Signal</th>'
               f'<th>Value</th></tr>{catalog_rows}</table>')

    original_by_signal = {row.get('signal'): row for row in calibration.get('originals', [])}
    write_rows = ''.join(
        f'<tr><td>{escape(row.get("order"))}</td><td>{escape(row.get("signal"))}</td>'
        f'<td>{escape(row.get("value"))}</td><td>{escape(row.get("readback_data"))}</td>'
        f'<td>{escape(row.get("verified"))}</td><td>{escape(row.get("write_started_monotonic"))}</td>'
        f'<td>{escape(row.get("readback_monotonic", row.get("monotonic")))}</td></tr>'
        for row in calibration.get('writes', []))
    restoration_rows = ''.join(
        f'<tr><td>{escape(row.get("signal"))}</td>'
        f'<td>{escape(original_by_signal.get(row.get("signal"), {}).get("data"))}</td>'
        f'<td>{escape(row.get("readback_data"))}</td><td>{escape(row.get("verified"))}</td>'
        f'<td>{escape(row.get("write_started_monotonic"))}</td>'
        f'<td>{escape(row.get("readback_monotonic", row.get("monotonic")))}</td></tr>'
        for row in calibration.get('restoration', []))
    evidence = ('<h2>XCP ordered-write evidence</h2><table><tr><th>Order</th><th>Signal</th>'
                '<th>Value</th><th>Readback</th><th>Verified</th><th>Write start</th>'
                f'<th>Readback time</th></tr>{write_rows}</table>'
                '<h2>Reverse restoration evidence</h2><table><tr><th>Signal</th><th>Original</th>'
                '<th>Readback</th><th>Verified</th><th>Restore start</th>'
                f'<th>Readback time</th></tr>{restoration_rows}</table>')
    return catalog, evidence


def vector_time_origin(result, frames):
    if 'vector_connection_epoch_s' in result:
        return result['vector_connection_epoch_s'], 'Vector connection'
    # Historical captures did not save the connection instant; do not invent it.
    return (frames[0]['timestamp'] if frames else 0), 'First recorded CAN frame (legacy capture)'


def build_unavailable(out, result):
    """Same reporting entry module also reports failures before evidence exists."""
    case = result['test_case']
    title = case_title(case)
    reason = result.get('error') or result.get('report_error') or 'Evidence unavailable'
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:15px system-ui;max-width:1250px;margin:28px auto;padding:0 24px}}h1{{font-size:24px}}</style>
<h1>{html.escape(title)} — BLOCKED</h1>
<p>Selection {escape(case.get('selection_id'))} · Excel row {escape(case.get('excel_row'))} · {escape(case.get('expected_fault'))}</p>
<p>{escape(reason)}</p><p>Injection: {escape(result.get('injection','NOT_EXECUTED'))}.
Recovery: {escape(result.get('recovery','NOT_EXECUTED'))}.</p>
<p>No complete DAQ/CAN evidence report is available. Timing limits not evaluated. DTC not implemented.</p></html>'''
    (out/'report.html').write_text(document, encoding='utf-8')


def pair_frames(frames):
    grouped={i:[f for f in frames if f['id']==i and f['rx'] and not f['extended']] for i in (0x11A,0x11B,0x11C)}
    if any(len(v)!=1 for v in grouped.values()):
        result={'pairing':'MISSING_OR_AMBIGUOUS','counts':{hex(k):len(v) for k,v in grouped.items()}}
        for first,second,key in ((0x11A,0x11B,'11B_minus_11A_ms'),(0x11B,0x11C,'11C_minus_11B_ms')):
            if len(grouped[first])==len(grouped[second])==1:
                delta=grouped[second][0]['timestamp']-grouped[first][0]['timestamp']
                if delta>=0:
                    result[key]=delta*1000
        return result
    a,b,c=(grouped[i][0]['timestamp'] for i in grouped)
    if not a<=b<=c:
        return {'pairing':'ORDER_INVALID'}
    return {'pairing':'UNIQUE_ORDERED_TRIPLET','11B_minus_11A_ms':(b-a)*1000,
            '11C_minus_11B_ms':(c-b)*1000,'11C_minus_11A_ms':(c-a)*1000,
            'interpretation':'USER_CONFIRMED_A_FAULT_B_REPORTED_C_RESPONSE'}


def supporting_signal_summary(rows):
    """Summarize DAQ evidence without using it for product judgment."""
    if not rows:
        return {}
    names = sorted({name for row in rows for name in row.get('signals', {})})
    summary = {}
    for name in names:
        values = [row['signals'][name] for row in rows if name in row.get('signals', {})]
        if values:
            summary[name] = {
                'sample_count': len(values),
                'first': values[0],
                'last': values[-1],
                'minimum': min(values),
                'maximum': max(values),
                'unique_values': sorted(set(values)) if name == 'EcuStatus' else None,
                'role': 'SUPPORTING_EVIDENCE_ONLY',
            }
    return summary


def chart(rows,anchor,events,result):
    if not rows:
        return '<p>No DAQ data.</p>'
    names=sorted(rows[0]['signals'], key=lambda name: (0 if name.lower()=='ibus' else 2 if name=='EcuStatus' else 1))
    colors=['#147d92','#8a4cbb','#c46a16','#307540']
    left,right,top,bottom=120,820,120,410
    width=right+350+max(0,len(names)-2)*320
    x=lambda t:left+(t+1.15)/2.3*(right-left)
    parts=[f'<svg viewBox="0 0 {width} 485" role="img" aria-label="DAQ signals with separate labeled Y axes">']
    for t in (-1,-0.5,0,0.5,1):
        parts.append(f'<path d="M{x(t)} {top}V{bottom}" stroke="#e2e7ec"/><text x="{x(t)}" y="437" text-anchor="middle">{t:g}</text>')
    axis_label=result.get('plot_origin_label','Time relative to fault occurrence A (s)')
    parts.append(f'<text x="{(left+right)/2}" y="471" text-anchor="middle">{html.escape(axis_label)}</text>')
    for index,name in enumerate(names):
        color=colors[index%len(colors)]
        values=[r['signals'][name] for r in rows]
        lo,hi=min(values),max(values)
        pad=max((hi-lo)*0.12,0.5 if name=='EcuStatus' else 1)
        lo,hi=lo-pad,hi+pad
        if name.lower()=='ibus':
            # At least 2 A total span: 0.1 A occupies no more than 5%.
            lo=math.floor(lo*2)/2
            hi=math.ceil(hi*2)/2
        display_name='Ibus (A)' if name.lower()=='ibus' else name
        y=lambda v:bottom-(v-lo)/(hi-lo)*(bottom-top)
        axis=left if index==0 else right+(index-1)*320
        labelx=axis-95 if index==0 else axis+280
        parts.append(f'<path d="M{axis} {top}V{bottom}" stroke="{color}"/><text fill="{color}" transform="translate({labelx},{(top+bottom)/2}) rotate(-90)" text-anchor="middle">{html.escape(display_name)}</text>')
        ticks=sorted({int(v) for v in values}) if name=='EcuStatus' else [lo+(hi-lo)*i/4 for i in range(5)]
        if name.lower()=='ibus':
            step=max(0.5,math.ceil((hi-lo)/6*2)/2)
            ticks=[lo+i*step for i in range(int((hi-lo)/step)+1)]
        for value in ticks:
            label=result.get('state_labels',{}).get(str(value),f'UNKNOWN ({value})') if name=='EcuStatus' else (f'{value:.1f}' if name.lower()=='ibus' else f'{value:.3g}')
            dx,align=(-10,'end') if index==0 else (10,'start')
            parts.append(f'<text x="{axis+dx}" y="{y(value)+4}" text-anchor="{align}" fill="{color}">{html.escape(label)}</text>')
        points=[]
        previous = None
        for row in rows:
            if previous is not None and row['monotonic']-previous > max(result.get('daq_period_ms',10)*2.5/1000,0.03):
                poly=' '.join(f'{a:.2f},{b:.2f}' for a,b in points)
                parts.append(f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>')
                points=[]
            px,py=x(row['monotonic']-anchor),y(row['signals'][name])
            if name=='EcuStatus' and points:
                points.append((px,points[-1][1]))
            points.append((px,py))
            previous = row['monotonic']
        poly=' '.join(f'{a:.2f},{b:.2f}' for a,b in points)
        parts.append(f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/><text x="{left+index*300}" y="30" fill="{color}">{html.escape(display_name)}</text>')
    for index,(tag,description,seconds,color) in enumerate(events):
        px=x(seconds)
        labely=60+index*20
        textx=px-12 if tag=='A' else px+12
        align='end' if tag=='A' else 'start'
        parts.append(f'<path d="M{px} {labely+5}V{bottom}" stroke="{color}" stroke-dasharray="5 4"/><text x="{textx}" y="{labely}" text-anchor="{align}" fill="{color}">{tag}: {description}</text>')
    return ''.join(parts)+'</svg>'


def build(out,result, report_name='report.html'):
    start=result['injection_start_monotonic']
    frames=[json.loads(s) for s in (out/'can.jsonl').read_text().splitlines()]
    vector_origin,vector_origin_label=vector_time_origin(result,frames)
    event_frames=[f for f in frames if f['id'] in (0x11A,0x11B,0x11C) and start<=f['monotonic']<=result.get('selector_restored_monotonic',start+3)]
    timing=pair_frames(event_frames)
    case = result.get('test_case')
    status = None
    if case:
        from recar.batch import evaluate_timing
        timing_evaluation = evaluate_timing(case, timing)
        timing.update({
            'mapping': timing_evaluation['mapping'],
            'fdti_limit_ms': timing_evaluation['fdti_limit_ms'],
            'fhti_limit_ms': timing_evaluation['fhti_limit_ms'],
            'fdti_verdict': timing_evaluation['fdti_verdict'],
            'fhti_verdict': timing_evaluation['fhti_verdict'],
            'timing_verdict': timing_evaluation['timing_verdict'],
        })
    anchor=start
    events=[]
    if timing['pairing']=='UNIQUE_ORDERED_TRIPLET':
        triplet=[next(f for f in event_frames if f['id']==n) for n in (0x11A,0x11B,0x11C)]
        anchor=triplet[0]['monotonic']
        events=[(tag,label,f['timestamp']-triplet[0]['timestamp'],color) for f,tag,label,color in zip(triplet,'ABC',('Fault occurrence','Fault reported','System response'),('#c54a3d','#d19315','#277d4f'))]
    rows=[json.loads(s) for s in (out/'daq.jsonl').read_text().splitlines()]
    before=[r for r in rows if r['monotonic']<=anchor-1]
    after=[r for r in rows if r['monotonic']>=anchor+1]
    bracket=[r for r in rows if before and after and before[-1]['monotonic']<=r['monotonic']<=after[0]['monotonic']]
    gaps=[(b['monotonic']-a['monotonic'])*1000 for a,b in zip(bracket,bracket[1:])]
    period=result.get('daq_period_ms',10)
    interruptions=[{'start_monotonic':a['monotonic'],'end_monotonic':b['monotonic'],
                    'gap_ms':(b['monotonic']-a['monotonic'])*1000}
                   for a,b in zip(rows,rows[1:]) if (b['monotonic']-a['monotonic'])*1000>max(period*2.5,30)]
    coverage=bool(gaps) and max(gaps)<=max(period*2.5,30) and not result.get('daq_error')
    names=list(rows[0]['signals']) if rows else []
    with (out/'injection_window.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream)
        writer.writerow(['relative_to_A_host_aligned_seconds',*names,'timestamp0','timestamp1'])
        for row in bracket:
            writer.writerow([row['monotonic']-anchor,*[row['signals'][n] for n in names],row['timestamp0'],row['timestamp1']])
    excluded_frames=[f for f in frames if f['id'] in (0x11A,0x11B,0x11C) and f['monotonic']>result.get('selector_restored_monotonic',start+3)]
    evidence={'window_complete':coverage,'window_sample_count':len(bracket),'max_sample_gap_ms':max(gaps) if gaps else None,
              'daq_interruptions':interruptions,
              'vector_time_origin_epoch_s':vector_origin,'vector_time_origin':vector_origin_label,
              'anchor':'A frame reception; DAQ host alignment approximate; CAN intervals use Vector timestamps','timing':timing,'event_frames':event_frames,
              'supporting_signals':supporting_signal_summary(bracket)}
    if excluded_frames:
        evidence['excluded_after_restore_frames']=excluded_frames
    metadata = ''
    title = f'FN-20763 · Fault {result.get("fault_value",1)}'
    if case:
        from recar.batch import classify
        status = classify({**result, 'evidence': evidence})
        evidence['test_case'] = case
        title = case_title(case)
        metadata = (f'<p class="caption">Selection {escape(case.get("selection_id"))} · Excel row {escape(case.get("excel_row"))} · '
                    f'{escape(case.get("expected_fault"))}<br>'
                    f'FHTI limit: {escape(case.get("fhti_ms"))} ms · FDTI limit: {escape(case.get("fdti_ms"))} ms<br>'
                    f'Functional result: {escape(status.get("status"))} · Injection: {escape(result.get("injection","NOT_EXECUTED"))} · '
                    f'Restore: {escape(result.get("selector_restore","NOT_EXECUTED"))} · '
                    f'Recovery: {escape(result.get("recovery","NOT_EXECUTED"))}</p>')
        if result.get('hardware_classification'):
            metadata += (f'<p class="caption">Hardware classification: '
                         f'{escape(result.get("hardware_classification"))}</p>')
        calibration=result.get('calibration_evidence',{})
        definition, multi_evidence = ordered_write_metadata(case, calibration)
        metadata += definition
        if case.get('writes'):
            metadata += multi_evidence
        elif calibration:
            original=calibration.get('original',{}).get('data','unavailable')
            written=calibration.get('injection',{}).get('readback_data','unavailable')
            restored=calibration.get('restoration',{}).get('readback_data','unavailable')
            metadata+=f'<p class="caption">XCP bytes: original {escape(original)} → injection readback {escape(written)} → restoration readback {escape(restored)}.</p>'
    (out/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    labels={0x11A:'A · Fault occurrence',0x11B:'B · Fault reported',0x11C:'C · System response'}
    table=''.join(f'<tr><td>{labels[f["id"]]}{" (excluded: after restore)" if f in excluded_frames else ""}</td><td>{f.get("channel","CAN1")}</td><td>0x{f["id"]:03X}</td><td>{f["timestamp"]-vector_origin:.6f}</td><td>{"Rx" if f["rx"] else "Tx"}</td><td>{f.get("dlc",8)}</td><td>{bytes.fromhex(f["data"]).hex(" ").upper()}</td></tr>' for f in event_frames+excluded_frames)
    intervals=''.join(f'<span>{label}: <b>{timing[key]:.3f} ms</b></span>' for key,label in (('11B_minus_11A_ms','FDTI (A → B)'),('11C_minus_11B_ms','FRTI (B → C)'),('11C_minus_11A_ms','FHTI (A → C)')) if key in timing)
    counts={tag:sum(frame['id']==identifier for frame in event_frames)
            for tag,identifier in (('A',0x11A),('B',0x11B),('C',0x11C))}
    def timing_cell(key):
        return f'{timing[key]:.3f}' if key in timing else 'N/A'
    functional_table = ''
    if status:
        functional_table = (
            '<h2>Functional judgment</h2><table><tr><th>A</th><th>B</th><th>C</th>'
            '<th>A→B / ms</th><th>B→C / ms</th><th>A→C / ms</th>'
            '<th>FDTI verdict</th><th>FHTI verdict</th><th>Final functional result</th></tr><tr>'
            f'<td>{counts["A"]}</td><td>{counts["B"]}</td><td>{counts["C"]}</td>'
            f'<td>{timing_cell("11B_minus_11A_ms")}</td>'
            f'<td>{timing_cell("11C_minus_11B_ms")}</td>'
            f'<td>{timing_cell("11C_minus_11A_ms")}</td>'
            f'<td>{escape(status["fdti_verdict"])}</td>'
            f'<td>{escape(status["fhti_verdict"])}</td>'
            f'<td>{escape(status["status"])}</td></tr></table>')
    support=evidence['supporting_signals']
    ibus=support.get('ibus',{})
    ecu=support.get('EcuStatus',{})
    ibus_text = f'{ibus["minimum"]:.3f} … {ibus["maximum"]:.3f}' if ibus else 'N/A'
    support_table=(
        '<h2>Supporting evidence</h2><table><tr><th>Warning Lamp</th><th>Ibus / A</th>'
        '<th>EcuStatus</th><th>Role</th></tr><tr>'
        f'<td>{escape(result.get("warning_observed"))}</td><td>{ibus_text}</td>'
        f'<td>{escape(ecu.get("unique_values"))}</td>'
        '<td>Evidence only; no functional verdict effect</td></tr></table>')
    warning='' if coverage else '<p class="warning">Incomplete ±1 s DAQ coverage.</p>'
    if interruptions:
        warning+=f'<p class="warning">DAQ gaps: {len(interruptions)}; maximum {max(g["gap_ms"] for g in interruptions):.3f} ms. Curves are broken across gaps; no samples filled.</p>'
    if not events:
        warning+='<p class="warning">Incomplete event triplet in injection window. Only unique available intervals are shown. Plot origin: host write start.</p>'
    chart_result=result if events else {**result,'plot_origin_label':'Time relative to injection request (s)'}
    chart_events=events or [('I','Injection request',0,'#777777')]
    plot=chart([r for r in rows if abs(r['monotonic']-anchor)<=1.1],anchor,chart_events,chart_result)
    fault_value=result.get('fault_value',1)
    document=f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:15px system-ui;color:#20333d;max-width:1250px;margin:28px auto;padding:0 24px}}h1{{font-size:24px}}h2{{font-size:18px}}svg{{width:100%;background:#f6f9fc}}svg text{{font:13px system-ui}}table{{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}}th,td{{padding:9px 7px;border-bottom:1px solid #dce3e8;text-align:left}}.intervals{{display:flex;gap:36px;margin:20px 0}}.caption{{font-size:12px;color:#647482}}.warning{{color:#ac4920}}</style>
<h1>{html.escape(title)} <small>— {period} ms DAQ</small></h1>{metadata}{warning}{plot}{functional_table}
<h2>CAN event frames</h2><table><tr><th>Event</th><th>Channel</th><th>ID</th><th>Vector elapsed / s</th><th>Dir</th><th>DLC</th><th>Data</th></tr>{table}</table>
<div class="intervals">{intervals}</div>{support_table}<p class="caption">Time zero: {html.escape(vector_origin_label)}. CAN FD · FDTI: A (fault occurrence) → B (fault reported) · FRTI: B → C (system response) · FHTI: A → C.<br>DAQ/event alignment uses host reception time; CAN intervals use Vector timestamps. Limits are evaluated without an added tolerance. Warning Lamp, Ibus, and EcuStatus are supporting evidence only.</p></html>'''
    (out/report_name).write_text(document,encoding='utf-8')
    return evidence
