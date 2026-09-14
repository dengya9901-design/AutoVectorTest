# recarTest

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
