# Row 215 repeated-signal review

The `MultiSignalRunner` now supports a generic ordered write sequence with the
same signal more than once. It captures the original value once before the
first write, preserves it through later writes, and restores the signal once to
that original value after the sequence. Unique signals are restored in reverse
order of their last modification.

Excel Row 215 uses `PDC_Fault_Pemt_Test` twice: first `1`, then a value
described only as `>2`. The TSR does not provide a concrete executable second
value. Therefore Row 215 is **not added to the executable catalog** and remains
`SPECIAL_SEQUENCE` pending engineering confirmation of that value. No numeric
value was inferred from the inequality.

The A2L audit remains `BLOCKED_BY_OFFLINE_ENVIRONMENT`: the current sandbox
lacks `pya2l`, and the previously validated environment cannot be launched from
this sandbox. This is neither a product failure nor a catalog failure.
