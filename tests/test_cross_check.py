"""Tests for the safe cross-check and the session pairing (no modem, no display)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import smoke_cross_check as cc


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


SHA = "2ad94260f24729d5007b505d1f8f98b3170cd3280f52cfff80417a7d3b5f7820"


def sender(tag, bw_when="before", size=200, **extra):
    return {"mode": "send", "tag": tag, "run_id": f"{tag}_A", "bw_when": bw_when, "bw_command": "BW500",
            "payload_bytes": size, "payload_sha256": SHA, "arq_confirmed": True, "sender_completed": True,
            "mycall": "XQ2CG-1", "destination": "XQ2CG-2", "modem": "mercury", "arm": "Mercury 1.9.19",
            "protocol_version": "5.0", "smoke_profile": "MS-1.0", "failures": [],
            "incomplete_evidence": [], **extra}


def receiver(tag, bw_when="before", size=200, **extra):
    return {"mode": "listen", "tag": tag, "run_id": f"{tag}_B", "bw_when": bw_when, "bw_command": "BW500",
            "declared_length": size, "payload_sha256": SHA, "delivered_intact": True,
            "mycall": "XQ2CG-2", "peer_call": "XQ2CG-1", "modem": "mercury", "arm": "Mercury 1.9.19",
            "protocol_version": "5.0", "smoke_profile": "MS-1.0", "failures": [],
            "incomplete_evidence": [], **extra}


# ---------------------------------------------------------------- one pair
check(cc.cross_check(sender("H1-1"), receiver("H1-1"))["outcome"] == "passed", "matching pair passes")
crossed = cc.cross_check(sender("E0-2_V2", "after"), receiver("H1-1", "before"))
check(crossed["outcome"] == "invalid_pair" and len(crossed["pair_mismatches"]) == 2,
      "E0-2 V2 against H1-1 is rejected although the hashes match")
check(crossed["sender_run_id"] == "E0-2_V2_A" and crossed["receiver_run_id"] == "H1-1_B", "run ids are reported")
check(cc.cross_check(sender("H1-1"), receiver("H1-1", modem="vara", arm="VARA HF unlicensed"))["outcome"]
      == "invalid_pair", "different modems are rejected")
check(cc.cross_check(sender("H1-1", modem="vara", arm="VARA HF licensed"),
                     receiver("H1-1", modem="vara", arm="VARA HF unlicensed"))["outcome"] == "invalid_pair",
      "a licensed sender against an unlicensed receiver is rejected")
check(cc.cross_check(sender("H1-1"), receiver("H1-1", mycall="XQ2CG-9"))["outcome"] == "invalid_pair",
      "a receiver with another callsign is rejected")
check(cc.cross_check(sender("H1-1", modem_binary_sha256="c69b8c81" * 8),
                     receiver("H1-1", modem_binary_sha256="42b217d9" * 8))["outcome"] == "invalid_pair",
      "VARA 4.9.0 on one PC and 4.9.1 on the other is rejected (different binaries)")
check(cc.cross_check(sender("H1-1", size=200), receiver("H1-1", size=1024))["outcome"] == "invalid_pair",
      "different payload sizes are rejected")
check(cc.cross_check(receiver("H1-1"), sender("H1-1"))["outcome"] == "invalid_pair",
      "sender and receiver in the wrong order are rejected")
legacy_s, legacy_r = sender("H1-1"), receiver("H1-1")
for result in (legacy_s, legacy_r):
    result.pop("modem"), result.pop("arm")
legacy_r.pop("peer_call")
check(cc.cross_check(legacy_s, legacy_r)["outcome"] == "passed", "alpha.2 results without modem still pair")
check(cc.cross_check(sender("H1-1"), receiver("H1-1", delivered_intact=False))["outcome"] == "failed",
      "a failed delivery fails")
check(cc.cross_check(sender("H1-1"), receiver("H1-1", infrastructure_fault=True))["outcome"]
      == "infrastructure_fault", "a MISSING SOUNDCARD run is an infrastructure fault")
check(cc.cross_check(sender("H1-1"), receiver("H1-1", incomplete_evidence=["x"]))["outcome"] == "incomplete",
      "missing evidence is incomplete")

# E0-3: the pair passes only when A verified B's ACK in the same session.
ack_line = "ACK run=0123456789ab SHA256=2ad94260f24729d5"
es = sender("E0-3_ACK", ack_mode=True, ack_verified=True, ack_line=ack_line, ack_round_trip_s=30.0)
er = receiver("E0-3_ACK", ack_mode=True, ack_sent=True, ack_line=ack_line)
check(cc.cross_check(es, er)["outcome"] == "passed", "E0-3 with a verified ACK passes")
check(cc.cross_check(dict(es, ack_verified=False), er)["outcome"] == "failed", "E0-3 without a verified ACK fails")
check(cc.cross_check(es, receiver("E0-3_ACK"))["outcome"] == "invalid_pair", "ACK on one side only is an invalid pair")
check(cc.cross_check(es, dict(er, ack_line="ACK run=ffffffffffff SHA256=2ad94260f24729d5"))["outcome"] == "failed",
      "an ACK that differs between A and B fails")

vs = sender("H1-1", modem="vara", arm="VARA HF licensed")
vr = receiver("H1-1", modem="vara", arm="VARA HF licensed", link_stats={"link_state": "unregistered"})
check(any("unregistered link" in w for w in cc.cross_check(vs, vr)["warnings"]),
      "a licensed arm on a link VARA reports as unregistered is flagged")
vr["arm"] = vs["arm"] = "VARA HF unlicensed"
check(cc.cross_check(vs, vr)["warnings"] == [], "an unlicensed arm on an unregistered link is consistent")

# ---------------------------------------------------------------- a whole session
root = Path(tempfile.mkdtemp(prefix="hf_arq_pair_"))


def write_run(base: Path, phase: str, tag: str, start: str, rid: str, result: dict) -> None:
    folder = base / phase / f"{tag}_{start}_{rid}"
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")


a, b = root / "A" / "results", root / "B_results"
# The sequence of 2026-10-22: B starts first, A follows within seconds.
write_run(a, "e0", "E0-1_16B", "20261022T165837Z", "e54675", sender("E0-1_16B", size=16))
write_run(b, "e0", "E0-1_16B", "20261022T165830Z", "33538c", receiver("E0-1_16B", size=16))
write_run(a, "e0", "E0-2_V2", "20261022T180139Z", "30147d", sender("E0-2_V2", "after"))
write_run(b, "e0", "E0-2_V2", "20261022T180134Z", "c2dc69", receiver("E0-2_V2", "after"))
write_run(a, "e0", "E0-2_V2", "20261022T182553Z", "04fe39", sender("E0-2_V2", "after"))   # crossed
write_run(b, "smoke", "H1-1", "20261022T182551Z", "e3e39e", receiver("H1-1"))             # crossed
write_run(a, "e0", "E0-2_V2", "20261022T183307Z", "5d0c40", sender("E0-2_V2", "after"))
write_run(b, "e0", "E0-2_V2", "20261022T183302Z", "6f5341", receiver("E0-2_V2", "after"))
write_run(a, "smoke", "H1-1", "20261022T183636Z", "88c4ff", sender("H1-1"))
write_run(b, "smoke", "H1-1", "20261022T183631Z", "0a2bac", receiver("H1-1"))
(b / "mercury_sessions" / "x").mkdir(parents=True)
(b / "mercury_sessions" / "x" / "result.json").write_text("{}", encoding="utf-8")

# A run stopped by the operator (no result.json) and one calibration per PC.
(b / "smoke" / "H1-2_20261022T184000Z_abcdef").mkdir(parents=True)
(b / "smoke" / "H1-2_20261022T184000Z_abcdef" / "events.jsonl").write_text("{}", encoding="utf-8")
for base, path, sinad, when in ((a, "B->A", 61.0, "2026-10-22T16:50:00+00:00"),
                                (b, "A->B", 58.0, "2026-10-22T16:49:00+00:00"),
                                (b, "A->B", 59.5, "2026-10-22T16:55:00+00:00")):
    folder = base / "calibration" / (when[:19].replace(":", "") + path.replace("->", "to"))
    folder.mkdir(parents=True)
    (folder / "calibration.json").write_text(json.dumps({
        "kind": "bench_calibration", "direction": path, "started_utc": when, "verdict": "ok",
        "channels": {"L": {"tone_found": True, "snr_db": 70.0, "sinad_db": sinad}},
        "max_simulated_snr_db": sinad - 10}), encoding="utf-8")

archive = root / "results_B.zip"
with zipfile.ZipFile(archive, "w") as z:
    for path in b.rglob("*"):
        if path.is_file():
            z.write(path, path.relative_to(b))

out = a / "cross_checks" / "t1"
report = cc.pair_session(a, archive, out)
pairs = {(p["sender_dir"], p["receiver_dir"]): p["outcome"] for p in report["pairs"]}
check(len(report["pairs"]) == 4 and set(pairs.values()) == {"passed"}, "four true pairs pass")
check(("H1-1_20261022T183636Z_88c4ff", "H1-1_20261022T183631Z_0a2bac") in pairs,
      "H1-1 pairs with the receiver of 18:36, not the one of 18:25")
check({u["dir"] for u in report["unpaired"]} ==
      {"E0-2_V2_20261022T182553Z_04fe39", "H1-1_20261022T182551Z_e3e39e"}, "the crossed runs are left unpaired")
check(report["crossed_runs"] == [{"sender": "E0-2_V2_20261022T182553Z_04fe39",
                                  "receiver": "H1-1_20261022T182551Z_e3e39e",
                                  "sender_tag": "E0-2_V2", "receiver_tag": "H1-1"}],
      "the crossed run is reported")
check(report["outcome"] == "review", "a session with a crossed run needs review")
check(report["stopped_runs"] == [{"dir": "H1-2_20261022T184000Z_abcdef", "tag": "H1-2", "pc": "other PC"}],
      "a stopped run without result.json is listed")
latest = report["calibration"]["latest"]
check(set(latest) == {"A->B", "B->A"} and latest["A->B"]["sinad_db"] == 59.5 and latest["B->A"]["pc"] == "this PC",
      "the latest calibration of each path is in the session report")
check((out / "cross_check_report.json").is_file() and (out / "cross_check_report.csv").is_file(),
      "the session report is written as JSON and CSV")
extracted = out / "other_pc" / "results_B"
check(len(list(extracted.rglob("cross_check.json"))) == 4, "cross_check.json is written beside each receiver")

# A second pairing must not pick up the copy of the other PC stored under cross_checks.
again = cc.pair_session(a, archive, a / "cross_checks" / "t2")
check(len(again["pairs"]) == 4 and len(again["unpaired"]) == 2, "earlier extractions are not paired again")

# ---------------------------------------------------------------- unsafe ZIP
evil = root / "evil.zip"
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("../../outside.txt", "x")
try:
    cc.safe_extract(evil, root / "evil_out")
    check(False, "a ZIP with ../ paths is refused")
except ValueError:
    check(not (root / "outside.txt").exists(), "a ZIP with ../ paths is refused")
shutil.rmtree(root)

# ---------------------------------------------------------------- Phase 1: simulator and cells
low, high = cc.wilson(8, 10)
check(abs(low - 0.4902) < 1e-3 and abs(high - 0.9433) < 1e-3, "Wilson interval for 8/10 is 49.0-94.3 %")
check(cc.wilson(0, 0) == (None, None) and cc.median([3, 1, 2]) == 2, "empty cell and median")
from datetime import datetime, timedelta, timezone
t0 = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)


def p1_run(k, outcome_ok=True, deadline=False):
    tag = f"P1-moderate-200B-{k:02d}"
    em = sender(tag, ack_mode=True, ack_verified=outcome_ok, ack_line="ACK", condition="moderate",
                delivery_time_s=40.0 + k, deadline_reached=deadline, sender_completed=outcome_ok)
    rx = receiver(tag, ack_mode=True, ack_sent=outcome_ok, ack_line="ACK", condition="moderate",
                  delivered_intact=outcome_ok)
    return {"tag": tag, "start": t0 + timedelta(minutes=3 * k), "result": em}, em, rx


sims = [{"attempt": k, "mode": "moderate", "start": t0 + timedelta(minutes=3 * k, seconds=8), "valid": k != 2,
         "problems": [] if k != 2 else ["audio dropouts {'input_overflow': 1}"], "pc": "this PC", "log": "x"}
        for k in (1, 2, 3, 4)]
sims[3]["mode"] = "poor"
for sim_row in sims:
    sim_row.update(arm="Mercury 1.9.19", snr_reference="rms", effective_snr_db={"A->B": 3.1, "B->A": 2.9})
rows = []
for k, ok in ((1, True), (2, True), (3, False), (4, True)):
    s_run, em, rx = p1_run(k, ok, deadline=not ok)
    row = cc.cross_check(em, rx)
    cc.attach_simulator(row, s_run, {"start": s_run["start"] - timedelta(seconds=30)}, sims)
    rows.append(row)
check([r["outcome"] for r in rows] == ["passed", "not_counted", "failed", "not_counted"],
      "a dropout or another condition in the simulator makes the attempt not counted")
check("trace" not in rows[0] and rows[0]["simulator"]["attempt"] == 1, "the simulator attempt is attached")
lonely_run, em, rx = p1_run(9)
lonely = cc.cross_check(em, rx)
cc.attach_simulator(lonely, lonely_run, {"start": lonely_run["start"] + timedelta(hours=3)}, sims)
check(lonely["outcome"] == "not_counted" and "no simulator attempt" in lonely["not_counted_reasons"][0],
      "a run without a simulator attempt is not counted")
cells = cc.cell_table(rows)
c = cells[0]
check(len(cells) == 1 and (c["attempts"], c["counted"], c["successes"], c["failures"], c["deadline_reached"])
      == (4, 2, 1, 1, 1) and c["median_delivery_s"] == 41.0 and len(c["not_counted"]) == 2,
      "the cell counts 1 success and 1 failure out of 2 counted, median 41 s")
mixed = [dict(rows[0], modem_binary_sha256="a" * 64), dict(rows[2], modem_binary_sha256="b" * 64)]
check(cc.cell_table(mixed)[0].get("warning", "").startswith("the modem binary changed"),
      "a cell whose modem version changed is flagged")
check(cells[0]["median_effective_snr_db"] == 3.1, "the cell reports the median effective RMS SNR")
other_arm = [dict(x, arm="VARA HF licensed") for x in sims]
s_run, em, rx = p1_run(1)
row = cc.cross_check(em, rx)
cc.attach_simulator(row, s_run, {"start": s_run["start"] - timedelta(seconds=30)}, other_arm)
check(row["outcome"] == "not_counted" and "noise was set for VARA HF licensed" in row["not_counted_reasons"][0],
      "noise set for another arm makes the attempt not counted")
print("ALL CROSS-CHECK TESTS PASSED")
