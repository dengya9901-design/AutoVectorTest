"""Catalog data for confirmed physical PARAMETER_OFFSET variants; no A2L scaling."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DEFS={
3:[('DEFAULT',[('Temp_Test',1,'=1',''),('Temp_Inject_Test',126,'>125','')])],
4:[('HIGH',[('Temp_Senor_Test',1,'=1',''),('TempVoltage_Test',4.96,'>4.95','V')]),('LOW',[('Temp_Senor_Test',1,'=1',''),('TempVoltage_Test',0.03,'<0.05','V')])],
5:[('DEFAULT',[('Over_Temp_Inject_Start',1,'=1',''),('Over_Temp_Warning_Inject',0.7,'<0.8','')])],
137:[('HIGH',[('Sensor_5V_Test',1,'=1',''),('Power_Source5V_Test',5.6,'>5.5','V')]),('LOW',[('Sensor_5V_Test',1,'=1',''),('Power_Source5V_Test',4.4,'<4.5','V')])],
197:[('DEFAULT',[('curr_switch',1,'=1',''),('IA_Curr_Offset',31,'>30','')])],
211:[('DEFAULT',[('Torque_AR_Fault_Test',1,'=1',''),('Aligning_Torque_Test',11.1,'>10.1','MotorToq')])],
212:[('DEFAULT',[('Torque_Damping_Fault_Test',1,'=1',''),('Damping_Torque_Test',10,'>9','MotorToq')])],
213:[('DEFAULT',[('Torque_FR_Fault_Test',1,'=1',''),('Friction_Torque_Value',1.5,'>0.5','MotorToq')])],
214:[('DEFAULT',[('PDC_Fault_Test',1,'=1',''),('PDC_Angle_Value_Test',3,'>2','')])],
218:[('DEFAULT',[('AimTorqueCheckThreshold',10,'=10','MotorTorq'),('BasicTorque_Switch',1,'=1',''),('BasicTorque_Offset',-6.9,'=-6.9','MotorTorq')])],
219:[('DEFAULT',[('AimTorqueCheckThreshold',10,'=10','MotorTorq'),('BasicTorque_Switch',1,'=1',''),('BasicTorque_Offset',6.9,'=6.9','MotorTorq')])],
220:[('DEFAULT',[('BasicTorque_Switch',1,'=1',''),('BasicTorque_Offset',-6.9,'=-6.9','MotorTorq')])],
221:[('DEFAULT',[('BasicTorque_Switch',1,'=1',''),('BasicTorque_Offset',6.9,'=6.9','MotorTorq')])],}
def main():
 p=ROOT/'recar/catalog.json'; c=json.loads(p.read_text(encoding='utf8')); src=json.loads((ROOT/'recarTest/remaining_hy_classification.json').read_text(encoding='utf8'))
 c['cases']=[x for x in c['cases'] if x['family']!='PARAMETER_OFFSET']; byrow={x['excel_row']:x for x in src['records']}
 for row,variants in DEFS.items():
  s=byrow[row]; aggregate='HARDWARE_VALIDATION_PENDING_ALL_VARIANTS' if len(variants)>1 else 'NOT_APPLICABLE'
  for n,items in variants:
   writes=[{'signal':a,'value':b,'physical_value':b,'raw_value':None,'source_expression':e,'unit':u,'order':i} for i,(a,b,e,u) in enumerate(items,1)]
   c['cases'].append({'family':'PARAMETER_OFFSET','selection_id':2000+row+(0 if n=='DEFAULT' else (100 if n=='HIGH' else 200)),'excel_row':row,'tsr_id':s['tsr_id'],'injection_signal':None,'injection_value':None,'expected_fault':s['fault_name'],'fhti_ms':s['fhti_ms'],'fdti_ms':s['fdti_ms'],'enabled':True,'description':'Ordered physical parameter injection','source_cells':{'fault_name':f'E{row}','injection':f'G{row}','enabled':f'H{row}'},'implementation_status':'IMPLEMENTED','offline_validation_status':'A2L_VALIDATION_REQUIRED','hardware_validation_status':'HARDWARE_VALIDATION_PENDING','hardware_execution_status':'NOT_RUN','recovery_validation_status':'HARDWARE_VALIDATION_REQUIRED','notes':'Physical values only; raw ECU conversion requires A2L validation. Confirmed >x executes as x+1.','writes':writes,'source_step':s['g_column_test_step'],'variant_id':n,'aggregate_row_status':aggregate})
 assert len([x for x in c['cases'] if x['family']=='PARAMETER_OFFSET'])==15
 p.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
if __name__=='__main__': main()
