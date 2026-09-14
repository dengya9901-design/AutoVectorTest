"""DAQ callback/JSONL pattern migrated from CANape/core/live_daq.py."""
import json
import threading
import time

from pyxcp.daq_stim import DaqList, DaqOnlinePolicy


class EvidenceDaq(DaqOnlinePolicy):
    def __init__(self, symbols, out, period_ms=10):
        types = {"SLONG": "I32", "FLOAT32_IEEE": "F32", "UWORD": "U16"}
        self.symbols = {s.name: s for s in symbols}
        self.period_ms = period_ms
        measurements = [(name, s.address, s.address_extension, types[s.data_type]) for name, s in self.symbols.items()]
        super().__init__([DaqList(name="evidence", event_num={100: 3, 10: 2, 1: 0}[period_ms],
                                 stim=False, enable_timestamps=True, measurements=measurements,
                                 prescaler=1, priority=0)])
        self.out = out
        self.rows = []
        self.lock = threading.Lock()
        self.stream = None
        self.running = False
        self.error = None

    def initialize(self):
        self.names = [h[0] for h in self.daq_lists[0].headers]
        self.stream = (self.out / "daq.jsonl").open("w", encoding="utf-8", buffering=1)

    def on_daq_list(self, daq_list, timestamp0, timestamp1, payload):
        try:
            raw = dict(zip(self.names, payload, strict=True))
            record = {"monotonic": time.monotonic(), "timestamp0": timestamp0, "timestamp1": timestamp1,
                      "raw": raw, "signals": {k: v*self.symbols[k].conversion_factor+self.symbols[k].conversion_offset for k,v in raw.items()}}
            with self.lock:
                self.stream.write(json.dumps(record) + "\n")
                self.rows.append(record)
        except Exception as exc:
            self.error = str(exc)

    def start(self):
        super().start()
        self.running = True

    def stop(self):
        if self.running:
            super().stop()
            self.running = False

    def finalize(self):
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = None

    def pretrigger_stats(self, now=None):
        with self.lock:
            rows = list(self.rows)
            error = self.error
        now = time.monotonic() if now is None else now
        recent = [r for r in rows if now-r["monotonic"] < 1.3]
        allowed_gap = max(self.period_ms / 1000 * 2.5, 0.03)
        gaps = [b["monotonic"]-a["monotonic"] for a,b in zip(recent,recent[1:])]
        duration = recent[-1]["monotonic"]-recent[0]["monotonic"] if recent else 0
        last_age = now-recent[-1]["monotonic"] if recent else None
        count_ok = len(recent) >= int(1000/self.period_ms)
        fresh = last_age is not None and last_age <= allowed_gap
        duration_ok = duration >= 1
        gap_ok = bool(gaps) and max(gaps) <= allowed_gap
        passed = not error and count_ok and fresh and duration_ok and gap_ok
        return {
            "status": "PASSED" if passed else "FAILED",
            "fresh": fresh,
            "sample_count": len(recent),
            "window_duration_ms": duration*1000,
            "maximum_gap_ms": max(gaps)*1000 if gaps else None,
            "allowed_gap_ms": allowed_gap*1000,
            "gaps_above_allowed": sum(gap > allowed_gap for gap in gaps),
            "last_sample_age_ms": last_age*1000 if last_age is not None else None,
            "daq_error": error,
            "acquisition_scope": "CURRENT_CONNECTION_ONLY",
        }

    def require_pretrigger(self, stats=None):
        stats = self.pretrigger_stats() if stats is None else stats
        if (stats["daq_error"] or stats["sample_count"] < int(1000/self.period_ms)
                or not stats["fresh"]):
            raise RuntimeError("DAQ missing/stale before injection: " + str(stats["daq_error"]))
        if stats["window_duration_ms"] < 1000:
            raise RuntimeError("DAQ pretrigger window shorter than 1s")
        if stats["gaps_above_allowed"]:
            raise RuntimeError("DAQ pretrigger has a data gap")
        return stats

    def latest(self):
        with self.lock:
            row = self.rows[-1] if self.rows else None
        if self.error or row is None or time.monotonic()-row['monotonic'] > 0.1:
            raise RuntimeError('DAQ observation missing or stale: ' + str(self.error))
        return row
