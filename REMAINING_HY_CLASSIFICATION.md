# Recar remaining H=Y classification

This is an analysis-only deliverable. It does not alter the frozen 94-case
single-signal catalog, any runner, judgment rule, recovery behavior, or report.
The source was `C:/recar/REF/北美recar-TSR测试.xlsx`, sheet `工作表1`.

## Scope and result

The analysis starts with every H=`Y` row, then excludes only the existing 94
exact one-signal/one-value cases in the SENT, MCU, MCU_OS, and MAGCHIP
families. The remaining count is **70**. Vbus row 199 is retained in this
analysis as an explicitly excluded case; it is not added to the completed
single-signal scope.

The complete per-record table, including Excel row, TSR ID, fault name,
unaltered complete G-column step, FHTI, FDTI, source method, classification,
and proposed future workflow, is in
[remaining_hy_classification.json](remaining_hy_classification.json).

| Category | Cases | Excel rows | Representative G-column step | Future automation feasibility |
|---|---:|---|---|---|
| `MULTI_SIGNAL_FIXED` | 44 | 138–151, 153–155; 169–192; 224–226 | `FAULT_INJECT.Predriver_Fault=1`, then `FAULT_INJECT.Predriver_Fault_Test=<value>` | Good offline candidate for a data-driven generalized multi-signal flow; A2L availability remains to be checked later. |
| `PARAMETER_OFFSET` | 13 | 3–5, 137, 197, 211–214, 218–221 | `curr_switch=1`, then `IA_Curr_Offset=-30` | Candidate for a typed parameter-injection flow after scaling, writeability, and ranges are resolved. |
| `VOLTAGE_OPERATION` | 2 | 194, 196 | 8 V supply condition, XCP enable, then 12–18 V supply condition | Manual/special workflow: source requires physical supply conditions. |
| `HARDWARE_INJECTION` | 9 | 17–20, 30–33, 35 | G is blank; source method is `Hardware injection` | Manual/bench workflow. |
| `SPECIAL_SEQUENCE` | 1 | 215 | `PDC_Fault_Pemt_Test=1`, then the same signal is written `=2` | A generalized multi-signal flow must support ordered repeated writes; do not treat it as one assignment. |
| `NOT_AUTOMATABLE_CURRENTLY` | 1 | 199 | `Vbus_Fault_Test=1` | Explicitly excluded Vbus scope; keep out of normal XCP automation. |

## Reusable write patterns

The source specifies no delay, reset, restoration, or recovery instruction for
the remaining software-injection rows. The following ordering is only the order
written in G; no extra timing or restoration behavior is inferred.

| Pattern | Rows | Invariant writes | Variable writes | Recommended eventual runner |
|---|---|---|---|---|
| Predriver selector | 138–151, 153–155 | `FAULT_INJECT.Predriver_Fault=1` | `FAULT_INJECT.Predriver_Fault_Test=1–14,16–18` | Generalized `MultiSignalRunner` |
| A4412 selector | 169–192 | `FAULT_INJECT.A4412_Fault=1` | `FAULT_INJECT.A4412_Fault_Test=1–24` | Generalized `MultiSignalRunner` |
| Enable plus threshold/scaled parameter | 3–5, 137, 197, 211–214, 218–221 | Case-specific enable/switch, plus a threshold where row 218/219 specify one | Case-specific threshold, scaled voltage, torque, current, or offset | `ParameterInjectionRunner` |
| Motor payload | 224–226 | Case-specific motor-test enable | Two or three fixed test values per row | Generalized `MultiSignalRunner` with data-defined ordered writes |
| Ordered repeat write | 215 | First `PDC_Fault_Pemt_Test=1` | Then `PDC_Fault_Pemt_Test=2` | Generalized `MultiSignalRunner` with sequence semantics |

Rows 218 and 219 contain three ordered writes: the source first sets
`AimTorqueCheckThreshold=10`, then enables `BasicTorque_Switch=1`, then writes
`BasicTorque_Offset` to `-6.9` or `6.9`. Rows 220 and 221 use the latter two
writes only. These are related parameter patterns, but their source-defined
payloads must remain data-driven and case-specific.

## Cases outside normal XCP automation

- Rows **17–20, 30–33, and 35** require hardware injection according to the
  TSR; their G cells contain no substitute software procedure.
- Rows **194 and 196** require source-stated 8 V and/or 12–18 V supply
  conditions, so they need a controlled physical-voltage workflow.
- Row **199** is `Vbus_Fault_Test=1`; Vbus remains explicitly excluded from the
  single-signal scope and is classified `NOT_AUTOMATABLE_CURRENTLY`.

## Recommended next software phase

Implement **`MULTI_SIGNAL_FIXED`** next, after a separate design approval. It
has 44 cases, including two large selector groups with one invariant enable
write and one test-specific selector value. Its source steps are deterministic
XCP assignments and contain no stated physical bench condition. Use data-driven
ordered write lists in a generalized `MultiSignalRunner`; do not make one
Python program per fault. This analysis does not implement that work.
