"""Blocks (0.2.0-alpha.5): plan, resume, repeat list, bench-fault guard, settings lock,
simulator readiness, and the cross-check rules that blocks rely on. No modem needed."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "smoke"))
import smoke_cross_check as cc
import smoke_gui_logic as logic
import smoke_sequence as seq

ARM = "VARA HF licensed"


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def make_run(results: Path, tag: str, mode: str, when: datetime, arm: str = ARM, **extra) -> Path:
    folder = results / "p1" / f"{tag}_{stamp(when)}_abc123"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (folder / "result.json").write_text(json.dumps({"mode": mode, "tag": tag, "arm": arm, **extra}), encoding="utf-8")
    return folder


tmp = Path(tempfile.mkdtemp(prefix="hf_arq_blocks_"))
now = datetime.now(timezone.utc).replace(microsecond=0)

# ---------------------------------------------------------------- plan and resume
results = tmp / "r1"
plan, done, _ = seq.block_plan("listen", "c.toml", "pilot", results, ARM)
check(len(plan) == 20 and done == 0 and plan[0]["tag"] == "PILOT-good-200B-01" and plan[-1]["tag"] == "PILOT-poor-200B-10",
      "pilot block: Good then Poor, 10 attempts each")
plan, _, _ = seq.block_plan("send", "c.toml", "campaign", results, ARM)
check(len(plan) == 180 and [p["tag"] for p in plan[::30]] == [
    "P1-good-200B-01", "P1-good-10KiB-01", "P1-moderate-200B-01", "P1-moderate-10KiB-01",
    "P1-poor-200B-01", "P1-poor-10KiB-01"], "campaign block: condition by condition, both loads")
check("--ack" in plan[0]["args"] and plan[30]["args"][plan[30]["args"].index("--deadline-s") + 1] == "3600",
      "every attempt carries the ACK and its deadline")
for k in (1, 2, 3):
    make_run(results, f"PILOT-good-200B-{k:02d}", "listen", now - timedelta(hours=1, minutes=-k))
make_run(results, "PILOT-good-200B-04", "listen", now, arm="Mercury 1.9.19")     # another arm
make_run(results, "PILOT-good-200B-05", "send", now)                              # another role
stopped = results / "p1" / f"PILOT-good-200B-04_{stamp(now)}_dead00"
stopped.mkdir()
(stopped / "events.jsonl").write_text("{}\n", encoding="utf-8")                  # no result.json
plan, done, _ = seq.block_plan("listen", "c.toml", "pilot", results, ARM)
check(done == 3 and plan[0]["tag"] == "PILOT-good-200B-04" and len(plan) == 17,
      "resume: attempts finished on this PC for this arm and role are skipped; a stopped run is redone")

# ---------------------------------------------------------------- repeat list from a cross-check
rows = []
for k in range(1, 11):
    outcome = "not_counted" if k == 4 else ("failed" if k == 7 else "passed")
    if k != 9:                                                         # attempt 9 has no pair at all
        rows.append({"arm": ARM, "series": "PILOT", "condition": "poor", "load": "200B", "attempt": k,
                     "outcome": outcome})
rows.append({"arm": "Mercury 1.9.19", "series": "PILOT", "condition": "poor", "load": "200B", "attempt": 1,
             "outcome": "not_counted"})
rows.append({"arm": ARM, "series": "P1", "condition": "good", "load": "200B", "attempt": 30, "outcome": "passed"})
report = {"created_utc": now.isoformat(), "pairs": rows}
wanted = seq.repeat_list(report, ARM)
check(wanted[0] == ("PILOT", "poor", "200B", 4) and wanted[1] == ("PILOT", "poor", "200B", 9),
      "repeat: the not-counted and the missing attempts, with their own numbers (same trace)")
check(("PILOT", "poor", "200B", 7) not in wanted, "repeat: a failed attempt is a result, not repeated")
check(len([w for w in wanted if w[0] == "P1"]) == 29, "repeat: every missing attempt 1..30 of a started P1 cell")
check(all(w[0] != "PILOT" or w[3] != 1 for w in wanted), "repeat: only this arm")
r2 = tmp / "r2"
cc_dir = r2 / "cross_checks" / "20261112T200000Z"
cc_dir.mkdir(parents=True)
(cc_dir / "cross_check_report.json").write_text(json.dumps(report), encoding="utf-8")
plan, done, note = seq.block_plan("listen", "c.toml", "repeat", r2, ARM)
check(len(plan) == 31 and done == 0 and "20261112T200000Z" in note, "repeat block plan from the latest cross-check")
make_run(r2, "PILOT-poor-200B-04", "listen", now + timedelta(minutes=5))
make_run(r2, "PILOT-poor-200B-09", "listen", now - timedelta(days=1))           # older than the cross-check
plan, done, _ = seq.block_plan("listen", "c.toml", "repeat", r2, ARM)
check(done == 1 and plan[0]["tag"] == "PILOT-poor-200B-09",
      "repeat resumes: an attempt already repeated after the cross-check is not run again")
try:
    seq.block_plan("listen", "c.toml", "repeat", tmp / "empty", ARM)
    check(False, "repeat without a cross-check is refused")
except ValueError:
    check(True, "repeat without a cross-check is refused")

# ---------------------------------------------------------------- bench-fault guard
check(seq.bench_fault(None, "poor") is not None, "guard: no result is a bench fault")
check(seq.bench_fault({"exception": "x", "failures": ["exception: ConnectionRefusedError()"]}, "poor") is not None,
      "guard: a script error (modem not reachable) is a bench fault")
check(seq.bench_fault({"infrastructure_fault": True}, "good") is not None, "guard: infrastructure fault")
check(seq.bench_fault({"failures": ["no CONNECTED event was observed"]}, "moderate") is not None,
      "guard: no connection in Moderate points to the bench")
check(seq.bench_fault({"failures": ["no CONNECTED event was observed"]}, "poor") is None,
      "guard: no connection in Poor can be the channel: not a bench fault")
check(seq.bench_fault({"failures": ["deadline of 600 s reached before a verified ACK"]}, "good") is None,
      "guard: a deadline is a result")
r3 = tmp / "r3"
t0 = now - timedelta(seconds=2)
make_run(r3, "P1-good-200B-01", "send", now, failures=[])
check(seq.latest_result(r3, "P1-good-200B-01", t0) is not None
      and seq.latest_result(r3, "P1-good-200B-01", now + timedelta(minutes=1)) is None,
      "guard: only the result of the attempt just run is read")

# ---------------------------------------------------------------- settings lock (binary and Drive)
ini = tmp / "VARA.ini"
ini.write_text("[Soundcard]\nALC Drive Level=-7\n[Setup]\nRegistration Code=SECRET\n", encoding="latin-1")
cfg = {"modem": {"name": "vara", "arm": ARM}, "evidence": {"vara_ini": str(ini)},
       "vara_session": {"exe_sha256": "42b217d9" * 8}}
r4 = tmp / "r4"
problem, lock = seq.check_lock(r4, cfg, ARM)
check(problem is None and lock["drive_level_db"] == "-7", "lock: the first block records binary and Drive")
check("SECRET" not in (r4 / "arm_settings.json").read_text(encoding="utf-8"), "lock: no registration code is stored")
check(seq.check_lock(r4, cfg, ARM)[0] is None, "lock: same settings, block starts")
ini.write_text("[Soundcard]\nALC Drive Level=-3\n", encoding="latin-1")
problem, _ = seq.check_lock(r4, cfg, ARM)
check(problem is not None and "drive_level_db was -7, now -3" in problem, "lock: a changed Drive refuses the block")
ini.write_text("[Soundcard]\nALC Drive Level=-7\n", encoding="latin-1")
problem, _ = seq.check_lock(r4, dict(cfg, vara_session={"exe_sha256": "ffff" * 16}), ARM)
check(problem is not None and "modem_binary_sha256" in problem, "lock: another modem binary refuses the block")

# ---------------------------------------------------------------- simulator readiness (PC B)
r5 = tmp / "r5"
check("not running" in seq.simulator_problem(r5, ARM), "PC B: no simulator, no block")
status_dir = r5 / "simulator" / "20261112T200000Z_auto"
status_dir.mkdir(parents=True)


def status(mode: str, age_s: float) -> None:
    t = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    (status_dir / "status.json").write_text(json.dumps({"t_utc": t, "mode": mode}), encoding="utf-8")


status("moderate", 1)
check("Automatic" in seq.simulator_problem(r5, ARM), "PC B: a simulator in a manual mode is refused")
status("auto", 60)
check("not running" in seq.simulator_problem(r5, ARM), "PC B: a stale simulator status is refused")
status("auto", 1)
check("power reference" in seq.simulator_problem(r5, ARM), "PC B: an arm without a power reference is refused")
(r5 / "simulator" / "modem_power.json").write_text(json.dumps({ARM: {}}), encoding="utf-8")
check(seq.simulator_problem(r5, ARM) is None, "PC B: Automatic simulator with the arm's power reference: ready")
seq.announce(r5, plan[0], ARM, "listening", "id1")
entry = json.loads((r5 / "simulator" / "announce.json").read_text(encoding="utf-8"))
check(entry["tag"] == plan[0]["tag"] and entry["attempt"] == 9 and entry["condition"] == "poor"
      and entry["arm"] == ARM and entry["state"] == "listening", "PC B announces condition, attempt, arm and tag")
zip_path = seq.make_zip(r5, "B", ARM, "pilot")
check(Path(zip_path).is_file() and "VARAHFlicensed_pilot" in zip_path, "a block ends with a named results ZIP")
Path(zip_path).unlink()

# ---------------------------------------------------------------- cross-check rules used by blocks
def row(k: int, outcome: str = "passed") -> dict:
    return {"series": "P1", "arm": ARM, "condition": "good", "load": "200B", "attempt": k, "outcome": outcome,
            "delivery_time_s": 50.0}


rows = [row(1), row(2, "not_counted"), row(2), row(1)]
cells = cc.cell_table(rows)
check(cells[0]["counted"] == 2 and cells[0]["successes"] == 2 and rows[3]["outcome"] == "not_counted",
      "cross-check: an attempt counts once; a later run of a counted attempt is listed as not counted")
sender = {"tag": "P1-good-200B-03", "result": {"deadline_s": 600}, "start": now}
receiver = {"start": now - timedelta(seconds=5)}
sim = {"start": now, "mode": "good", "attempt": 3, "valid": True, "problems": [], "tag": "KIT-good-200B-03",
       "arm": ARM, "snr_reference": "rms", "effective_snr_db": {}}
r = {"outcome": "passed", "arm": ARM}
cc.attach_simulator(r, sender, receiver, [sim])
check(r["outcome"] == "not_counted" and any("announced" in x for x in r["not_counted_reasons"]),
      "cross-check: the simulator's announced tag must be the run's tag")
print("ALL BLOCK TESTS PASSED")
