# AutoVectorTest
For Recar_CAAS project.

## Get the project and inspect cases offline

```powershell
git clone https://github.com/dengya9901-design/AutoVectorTest.git
cd AutoVectorTest
python -B -m recar.run --list
python -B -m recar.run --list --family SENT
python -B -m recar.run --family SENT --case 1 --dry-run
```

Run these commands from the repository root with Python 3.10 or later.
The listing and catalog dry-run commands use the Python standard library
and do not connect to the ECU. Dry-run reports are created in `reports/dry_run`;
they are metadata previews, not hardware PASS evidence.

## Hardware environment and evidence

This public repository contains the Recar catalog, common runners, report generators,
and development handoff documents. The complete local hardware environment
is not bundled: `recar.parameter` and `recar.a2l_resolver`, the validated
Python/XCP environment, Vector drivers, CANoe configuration/DBC, active A2L,
and ECU software must be supplied separately by the project owner.
Test case files under `tests/` are kept locally and are not distributed in
the current public source tree. The local full test suite also requires
those Python dependencies. Hardware paths
currently refer to the validated Windows setup under `C:\recar`.

Catalog enablement and historical handoff metadata alone do not authorize
hardware execution. Check the latest engineering exclusions and campaign
status before selecting a hardware case. The current master status, raw
hardware evidence, and PASS delivery ZIP are maintained separately from this
source repository; request the current delivery package from the owner.

Functional acceptance requires A/B/C events, A→B <= catalog FDTI, and
A→C <= catalog FHTI. B→C, Warning Lamp, Ibus, and EcuStatus are supporting
functional evidence; ECU state still participates in safety baseline checks.

## Existing SENT batch interface

The formal SENT batch is catalog driven and contains exactly Excel Rows 7–15
and 22–28 (`FAULT_INJECT.Sent_Fault_Test = 1..16`) in selector order.

The hardware command is intentionally guarded:

```powershell
python -B -m recar.batch --execute-hardware --batch sent-all
```

Each case uses the common single-case probe and runner. The next case starts
only after restoration, `11 01`, at least five seconds of stabilization, XCP
reconnection/unlock, verified ECU/CAN baseline, and a fresh post-reset 10 ms
DAQ pretrigger. Functional outcomes do not abort the batch when recovery is
safe. Infrastructure or recovery failures abort it and mark every remaining
case `NOT_RUN_BATCH_ABORTED`.

Each run creates `batch_summary.json`, `batch_summary.html`, and links to the
individual case reports. Supplying `--batch sent-all` without
`--execute-hardware` cannot access the ECU.

Before a selector write, a failed DAQ quality window may be discarded and
reacquired in a new probe process at most twice. Reacquisition requires proof
that no injection write occurred, the selector remains at zero, and the ECU,
CAN, XCP, and DAQ infrastructure are otherwise healthy. The 10 ms DAQ maximum
gap remains 30 ms. No post-write failure is eligible for this retry.

A stopped SENT batch can reuse a verified completed prefix by explicitly
identifying its summary and the first selection to execute:

```powershell
python -B -m recar.batch --execute-hardware --batch sent-all `
  --resume-from 3 `
  --merge-from C:\recar\Autotest\reports\batch\20260914T194718115072Z\batch_summary.json
```

The merged summary retains the original individual report links and records
the execution session ID for every case. Prefix cases are never relaunched.
