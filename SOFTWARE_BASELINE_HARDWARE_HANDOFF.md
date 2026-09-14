# Recar software baseline and hardware-validation handoff

Baseline commit: `faff632 feat: add parameter offset catalog variants`.
This configuration is planning data only. It does not initiate XCP, CAN, DAQ,
UDS, CANoe, CANape, or bench actions.

## Frozen software baseline

| Source category | Source rows | Executable variants |
|---|---:|---:|
| SINGLE_SIGNAL | 94 | 94 |
| MULTI_SIGNAL_FIXED | 44 | 44 |
| SPECIAL_SEQUENCE | 1 | 1 |
| PARAMETER_OFFSET | 13 | 15 |
| **Implemented total** | **152** | **154** |

Rows 4 and 137 each have HIGH and LOW variants. A source row is not hardware
complete until both variants have `HARDWARE_VALIDATED` evidence.

`IMPLEMENTED` means catalog and generic execution support exist.
`OFFLINE_VERIFIED` means offline definition and regression validation completed.
`A2L_VALIDATION_REQUIRED` means physical values are retained but ECU raw
conversion has not been verified. `HARDWARE_VALIDATION_PENDING` means no
case-specific functional hardware validation has been recorded.
`HARDWARE_VERIFIED` is recovery/evidence status only, and
`HARDWARE_EXECUTED_INCOMPLETE` means executed evidence was incomplete; neither
is a product PASS.

Current counts: 139 `OFFLINE_VERIFIED`, 15 `A2L_VALIDATION_REQUIRED`, 152
`HARDWARE_VALIDATION_PENDING`, 2 `HARDWARE_VALIDATED`, 3 recovery records
`HARDWARE_VERIFIED`, and 1 `HARDWARE_EXECUTED_INCOMPLETE`.

## Validation matrix

The complete 154-entry queue is [HARDWARE_VALIDATION_MATRIX.json](HARDWARE_VALIDATION_MATRIX.json).
Each entry preserves source row, variant, category, fault, ordered physical
writes, FHTI/FDTI, A2L status, prior hardware status, stage, batch gate, and
stop conditions.

1. Stage 1: known-good SENT environment sanity.
2. Stage 2: MCU Row 37 investigation.
3. Stage 3: MCU_OS, MAGCHIP, Predriver, A4412, PARAMETER_OFFSET row 3, and
   Row 215 representative smoke tests.
4. Stage 4: family batches only after that family's representative has
   `HARDWARE_VALIDATED` evidence.

Parameter-offset variants are blocked from hardware execution until their A2L
physical-to-raw conversion is validated. A batch stops immediately on write or
readback failure, incomplete DAQ evidence, failed communication recovery,
failed restoration, missing expected evidence, unknown ECU state, or missing
required A2L conversion validation.

## Manual and hardware-dependent work

Keep these outside the automated software queue:

- `VOLTAGE_OPERATION`: rows 194 and 196.
- `HARDWARE_INJECTION`: rows 17–20, 30–33, and 35.
- Excluded Vbus: row 199.

The A2L audit remains blocked by the offline environment: the sandbox lacks
usable `pya2l` and cannot launch the previously validated external environment.
This is not a product or catalog failure.
