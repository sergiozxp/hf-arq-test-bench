"""smoke_cross_check.py - verify delivery between a sender and a receiver result.

USAGE
  One pair:
    python smoke_cross_check.py <sender result.json> <receiver result.json>
  Every pair of a session (this PC's results and the other PC's ZIP or folder):
    python smoke_cross_check.py --pair <local results folder> <other PC ZIP or folder>

WHAT IT DOES
  - Declares delivery only if the receiver verified the payload hash and it matches
    the sender's, and keeps that separate from the sender's ARQ confirmation (BUFFER 0).
  - Refuses to compare two different tests: the tag, bandwidth timing, payload size,
    modem, arm and callsigns of the two results must agree ("invalid_pair"). Two
    tests that share a payload (E0-2 V2 and H1 both send the same 200 bytes) would
    otherwise pass.
  - --pair matches every sender run with the receiver run of the same tag that
    started closest in time, writes cross_check.json beside each receiver result,
    and writes a session report (JSON and CSV). Runs left without a partner are
    listed, and a sender and a receiver that started together with different tags
    are reported as a crossed run.

WHAT IT DOES NOT
  - Run or repeat tests, or touch the modem.
  - Compare times between PCs: their clocks are not synchronised in the smoke test.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR_RE = re.compile(r"^(?P<tag>.+)_(?P<start>\d{8}T\d{6}Z)_(?P<rid>[0-9a-f]{6})$")
# A receiver is started first; the sender follows within this window (seconds,
# sender start minus receiver start). The negative margin absorbs clock offset.
PAIR_WINDOW_S = (-120, 600)
CROSSED_WINDOW_S = 120
P1_TAG_RE = re.compile(r"^(?P<series>P1|KIT|PILOT)-(?P<condition>[a-z]+)-(?P<load>[0-9A-Za-z]+)-(?P<attempt>\d+)$")
# The simulator runs on PC B, so its attempts are matched against PC B's runs (same clock):
# attempt k starts on PC A's first audio, after PC B's run k started listening.
SIM_EARLY_S = 0
WILSON_Z = 1.96


def _get(result: dict, *keys):
    for key in keys:
        if result.get(key) is not None:
            return result[key]
    return None


def pair_mismatches(em: dict, rx: dict) -> list[str]:
    """Return the reasons why two results do not belong to the same test."""
    reasons = []
    if em.get("mode") != "send":
        reasons.append("the first result is not a sender result")
    if rx.get("mode") != "listen":
        reasons.append("the second result is not a receiver result")
    if em.get("tag") != rx.get("tag"):
        reasons.append(f"different tests: sender {em.get('tag')}, receiver {rx.get('tag')}")
    if em.get("bw_when") != rx.get("bw_when"):
        reasons.append(f"bandwidth sent at different moments: sender {em.get('bw_when')}, "
                       f"receiver {rx.get('bw_when')}")
    if em.get("bw_command") != rx.get("bw_command"):
        reasons.append(f"different bandwidth: {em.get('bw_command')} / {rx.get('bw_command')}")
    sent = _get(em, "payload_bytes")
    declared = _get(rx, "declared_length")
    if sent is not None and declared is not None and sent != declared:
        reasons.append(f"different payload size: sender {sent} B, receiver {declared} B")
    for key, label in (("modem", "modem"), ("arm", "arm"),
                       ("protocol_version", "protocol version"), ("smoke_profile", "smoke profile")):
        a, b = em.get(key), rx.get(key)
        # Results written before 0.2.0-alpha.3 have no modem or arm: Mercury is assumed.
        if key in ("modem", "arm") and (a is None or b is None):
            continue
        if a != b:
            reasons.append(f"different {label}: {a} / {b}")
    a_bin, b_bin = em.get("modem_binary_sha256"), rx.get("modem_binary_sha256")
    if a_bin and b_bin and a_bin != b_bin:
        reasons.append(f"different modem binaries: {a_bin[:12]}… / {b_bin[:12]}… (same version on both PCs)")
    if em.get("condition") != rx.get("condition"):
        reasons.append(f"different channel condition: {em.get('condition')} / {rx.get('condition')}")
    if bool(em.get("ack_mode")) != bool(rx.get("ack_mode")):
        reasons.append(f"same-session ACK on one side only: sender {bool(em.get('ack_mode'))}, "
                       f"receiver {bool(rx.get('ack_mode'))}")
    if em.get("mycall") and rx.get("mycall"):
        dest = _get(em, "destination", "peer_call")
        if dest and dest.upper() != rx["mycall"].upper():
            reasons.append(f"the sender called {dest} but the receiver is {rx['mycall']}")
        if rx.get("peer_call") and rx["peer_call"].upper() != em["mycall"].upper():
            reasons.append(f"the receiver expected {rx['peer_call']} but the sender is {em['mycall']}")
    return reasons


def cross_check(em: dict, rx: dict) -> dict:
    """Compare one sender result with one receiver result."""
    hash_ok = em.get("payload_sha256") is not None and \
        em.get("payload_sha256") == rx.get("payload_sha256")
    warnings = []
    if em.get("arq_confirmed") and not rx.get("delivered_intact"):
        warnings.append("ARQ confirmed (BUFFER 0) but B does not verify the payload: delivery failed")
    if rx.get("delivered_intact") and not em.get("arq_confirmed"):
        warnings.append("B verified the payload but A did not observe BUFFER 0 (ARQ not observed)")
    if not hash_ok:
        warnings.append("sender and receiver SHA-256 differ or one is missing")
    incomplete = (em.get("incomplete_evidence") or []) + (rx.get("incomplete_evidence") or [])
    if incomplete:
        warnings.append(f"incomplete evidence (required by MS-1.0): {incomplete}")
    protocol = (em.get("protocol_warnings") or []) + (rx.get("protocol_warnings") or [])
    # VARA states the link mode itself ("LINK UNREGISTERED"); it must agree with the arm.
    for side, res in (("sender", em), ("receiver", rx)):
        state = (res.get("link_stats") or {}).get("link_state")
        arm = (res.get("arm") or "").lower()
        if state and ("licensed" in arm) and (("unlicensed" in arm) != (state == "unregistered")):
            warnings.append(f"{side}: VARA reported a {state} link but the arm is {res.get('arm')}")
    mismatches = pair_mismatches(em, rx)
    result = {
        "sender_tag": em.get("tag"), "receiver_tag": rx.get("tag"),
        "sender_run_id": em.get("run_id"), "receiver_run_id": rx.get("run_id"),
        "modem": em.get("modem", "mercury"), "arm": em.get("arm"),
        "sender_arq_confirmed": em.get("arq_confirmed"),
        "receiver_delivered_intact": rx.get("delivered_intact"),
        "sender_receiver_hash_matches": hash_ok,
        "sender_failures": em.get("failures"), "receiver_failures": rx.get("failures"),
        "sender_warnings": em.get("warnings"), "receiver_warnings": rx.get("warnings"),
        "protocol_warnings": protocol,
        "pair_mismatches": mismatches,
        "warnings": warnings,
        "protocol_version": em.get("protocol_version") or rx.get("protocol_version"),
        "smoke_profile": em.get("smoke_profile") or rx.get("smoke_profile"),
    }
    delivered = bool(hash_ok and rx.get("delivered_intact") and em.get("sender_completed"))
    if em.get("ack_mode"):
        # E0-3: delivery also requires A to have verified B's ACK in the same session.
        result["sender_ack_verified"] = em.get("ack_verified")
        result["ack_round_trip_s"] = em.get("ack_round_trip_s")
        result["receiver_ack_sent"] = rx.get("ack_sent")
        if em.get("ack_verified") is not True:
            warnings.append("E0-3: A did not verify B's ACK in the same session")
        if rx.get("ack_line") and em.get("ack_line") and rx["ack_line"] != em["ack_line"]:
            warnings.append("E0-3: the ACK read by A differs from the ACK written by B")
            delivered = False
        delivered = delivered and em.get("ack_verified") is True
    if mismatches:
        result["outcome"] = "invalid_pair"
    elif em.get("infrastructure_fault") or rx.get("infrastructure_fault"):
        result["outcome"] = "infrastructure_fault"
    elif em.get("procedure_fault") or rx.get("procedure_fault"):
        result["outcome"] = "procedure_fault"
    elif not delivered:
        result["outcome"] = "failed"
    else:
        # "incomplete": delivery verified, but evidence that MS-1.0 requires is missing.
        result["outcome"] = "incomplete" if incomplete else "passed"
    return result


# ------------------------------------------------------------------ session pairing
def safe_extract(archive: Path, destination: Path) -> Path:
    """Extract a ZIP, refusing members that would land outside the destination."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"unsafe path in ZIP: {member.filename}")
        z.extractall(destination)
    return destination


def find_runs(root: Path, origin: str) -> list[dict]:
    runs = []
    for path in sorted(root.rglob("result.json")):
        relative = path.relative_to(root).parts
        if "mercury_sessions" in relative or "cross_checks" in relative:
            continue
        match = RUN_DIR_RE.match(path.parent.name)
        if not match:
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        start = datetime.strptime(match.group("start"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        runs.append({"path": path, "dir": path.parent.name, "tag": match.group("tag"),
                     "start": start, "origin": origin, "mode": result.get("mode"),
                     "result": result})
    return runs


def find_stopped_runs(root: Path, origin: str) -> list[dict]:
    """Run folders that have an event log but no result.json (stopped or crashed runs)."""
    stopped = []
    for events in sorted(root.rglob("events.jsonl")):
        relative = events.relative_to(root).parts
        if "mercury_sessions" in relative or "cross_checks" in relative:
            continue
        match = RUN_DIR_RE.match(events.parent.name)
        if match and not (events.parent / "result.json").exists():
            stopped.append({"dir": events.parent.name, "tag": match.group("tag"), "pc": origin})
    return stopped


def find_calibrations(root: Path, origin: str) -> list[dict]:
    """Bench calibration records (results/calibration/<time>_<path>/calibration.json)."""
    found = []
    for path in sorted(root.rglob("calibration.json")):
        if "cross_checks" in path.relative_to(root).parts:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("kind") != "bench_calibration":
            continue
        left = (record.get("channels") or {}).get("L") or {}
        found.append({"pc": origin, "dir": path.parent.name, "direction": record.get("direction"),
                      "started_utc": record.get("started_utc"), "verdict": record.get("verdict"),
                      "snr_db": left.get("snr_db"), "sinad_db": left.get("sinad_db"),
                      "tone_peak_dbfs": left.get("tone_peak_dbfs"),
                      "clock_offset_ppm": left.get("clock_offset_ppm"),
                      "max_simulated_snr_db": record.get("max_simulated_snr_db"),
                      "reasons": record.get("reasons")})
    return found


def find_simulator_attempts(root: Path, origin: str) -> list[dict]:
    """Attempts recorded by the channel simulator (results/simulator/*/sim_log.jsonl)."""
    attempts = []
    for log in sorted(root.rglob("sim_log.jsonl")):
        if "cross_checks" in log.relative_to(root).parts:
            continue
        starts, ends = {}, {}
        try:
            for line in log.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("event") == "attempt_start":
                    starts[(record["attempt"], record["t_utc"])] = record
                elif record.get("event") == "attempt_end":
                    ends[(record["attempt"], record.get("started_utc"))] = record
        except (OSError, ValueError, KeyError):
            continue
        for key, start in starts.items():
            end = ends.get(key)
            directions = (end or {}).get("directions") or {}
            attempts.append({"log": log.parent.name, "pc": origin, "attempt": start["attempt"],
                             "mode": start.get("mode"), "arm": start.get("arm"),
                             "snr_reference": start.get("snr_reference"),
                             "effective_snr_db": {d: v.get("effective_snr_db") for d, v in directions.items()},
                             "start": datetime.fromisoformat(start["t_utc"]),
                             "valid": bool(end and end.get("valid")),
                             "problems": (end or {}).get("problems") or
                             ([] if end else ["the simulator stopped before the attempt ended"])})
    return attempts


def wilson(successes: int, n: int, z: float = WILSON_Z) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def attach_simulator(row: dict, sender: dict, receiver: dict, sim_attempts: list[dict]) -> None:
    """Phase 1 pairs: the simulator attempt must exist, match and be valid, or the pair is not counted."""
    match = P1_TAG_RE.match(sender["tag"])
    if not match:
        return
    condition, attempt = match.group("condition"), int(match.group("attempt"))
    row.update(series=match.group("series"), condition=condition, load=match.group("load"), attempt=attempt,
               delivery_time_s=sender["result"].get("delivery_time_s"),
               modem_binary_sha256=sender["result"].get("modem_binary_sha256"),
               deadline_reached=bool(sender["result"].get("deadline_reached")))
    limit = (sender["result"].get("deadline_s") or 3600) + 300
    near = sorted((a for a in sim_attempts
                   if -SIM_EARLY_S <= (a["start"] - receiver["start"]).total_seconds() <= limit),
                  key=lambda a: a["start"])
    reasons = []
    if not near:
        reasons.append("no simulator attempt starts while PC B's run was listening")
    else:
        sim = near[0]
        row["simulator"] = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in sim.items()}
        if sim["mode"] != condition:
            reasons.append(f"the simulator ran '{sim['mode']}', the run is '{condition}'")
        row["snr_reference"] = sim.get("snr_reference")
        row["effective_snr_db"] = (sim.get("effective_snr_db") or {}).get("A->B")
        if sim.get("snr_reference") == "rms" and sim.get("arm") and sim["arm"] != row.get("arm"):
            reasons.append(f"the simulator's noise was set for {sim['arm']}, the run is {row.get('arm')}")
        if sim["attempt"] != attempt:
            reasons.append(f"the simulator used attempt {sim['attempt']}'s trace for attempt {attempt}")
        if not sim["valid"]:
            reasons.extend(sim["problems"] or ["simulator attempt not valid"])
    if reasons and row["outcome"] in ("passed", "failed"):
        row["attempt_outcome"] = row["outcome"]
        row["outcome"] = "not_counted"
        row["not_counted_reasons"] = reasons


def cell_table(rows: list[dict]) -> list[dict]:
    cells: dict[tuple, dict] = {}
    for row in rows:
        if "condition" not in row:
            continue
        key = (row.get("series", "P1"), row.get("arm"), row["condition"], row["load"])
        cell = cells.setdefault(key, {"series": key[0], "arm": key[1], "condition": key[2], "load": key[3],
                                      "attempts": 0,
                                      "counted": 0, "successes": 0, "failures": 0, "deadline_reached": 0,
                                      "not_counted": [], "times": [], "binaries": set(), "snrs": []})
        cell["attempts"] += 1
        if row.get("modem_binary_sha256"):
            cell["binaries"].add(row["modem_binary_sha256"])
        if row["outcome"] in ("passed", "failed"):
            cell["counted"] += 1
            if row.get("effective_snr_db") is not None:
                cell["snrs"].append(row["effective_snr_db"])
            if row["outcome"] == "passed":
                cell["successes"] += 1
                if row.get("delivery_time_s") is not None:
                    cell["times"].append(row["delivery_time_s"])
            else:
                cell["failures"] += 1
                cell["deadline_reached"] += int(bool(row.get("deadline_reached")))
        else:
            cell["not_counted"].append({"attempt": row.get("attempt"), "outcome": row["outcome"],
                                        "reasons": row.get("not_counted_reasons") or row.get("warnings")})
    out = []
    for cell in cells.values():
        low, high = wilson(cell["successes"], cell["counted"])
        times = cell.pop("times")
        cell["modem_binaries"] = sorted(cell.pop("binaries"))
        snrs = cell.pop("snrs")
        cell["median_effective_snr_db"] = None if not snrs else round(median(snrs), 2)
        if len(cell["modem_binaries"]) > 1:
            cell["warning"] = "the modem binary changed during the cell (version updated?)"
        cell.update(success_rate=round(cell["successes"] / cell["counted"], 4) if cell["counted"] else None,
                    wilson_95=[low, high], median_delivery_s=None if not times else round(median(times), 1))
        out.append(cell)
    return sorted(out, key=lambda c: (c["series"], str(c["arm"]), c["condition"], c["load"]))


def pair_runs(runs: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair senders with receivers by tag and start time.

    Returns (pairs, unpaired runs, crossed runs). A crossed run is an unpaired sender
    and an unpaired receiver with different tags that started within two minutes of
    each other: the two PCs ran different tests at the same time.
    """
    senders = [r for r in runs if r["mode"] == "send"]
    receivers = [r for r in runs if r["mode"] == "listen"]
    candidates = []
    for s in senders:
        for r in receivers:
            delta = (s["start"] - r["start"]).total_seconds()
            if s["tag"] == r["tag"] and PAIR_WINDOW_S[0] <= delta <= PAIR_WINDOW_S[1]:
                candidates.append((abs(delta), id(s), id(r), s, r))
    candidates.sort(key=lambda item: item[0])
    used, pairs = set(), []
    for _gap, sid, rid, s, r in candidates:
        if sid in used or rid in used:
            continue
        used.update((sid, rid))
        pairs.append((s, r))
    pairs.sort(key=lambda pair: pair[0]["start"])
    unpaired = [r for r in senders + receivers if id(r) not in used]
    crossed = []
    for s in (r for r in unpaired if r["mode"] == "send"):
        for r in (r for r in unpaired if r["mode"] == "listen"):
            if s["tag"] != r["tag"] and abs((s["start"] - r["start"]).total_seconds()) <= CROSSED_WINDOW_S:
                crossed.append({"sender": s["dir"], "receiver": r["dir"],
                                "sender_tag": s["tag"], "receiver_tag": r["tag"]})
    return pairs, unpaired, crossed


def pair_session(local: Path, other: Path, out_dir: Path) -> dict:
    """Cross-check every pair found in this PC's results and the other PC's results."""
    if other.is_file() and other.suffix.lower() == ".zip":
        other = safe_extract(other, out_dir / "other_pc" / other.stem)
    runs = find_runs(local, "this PC") + find_runs(other, "other PC")
    pairs, unpaired, crossed = pair_runs(runs)
    sim_attempts = find_simulator_attempts(local, "this PC") + find_simulator_attempts(other, "other PC")
    rows = []
    for s, r in pairs:
        result = cross_check(s["result"], r["result"])
        attach_simulator(result, s, r, sim_attempts)
        result["sender_dir"], result["receiver_dir"] = s["dir"], r["dir"]
        result["sender_pc"], result["receiver_pc"] = s["origin"], r["origin"]
        result["sender_start_utc"] = s["start"].isoformat()
        result["receiver_start_utc"] = r["start"].isoformat()
        r["path"].with_name("cross_check.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(result)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "local_results": str(local), "other_results": str(other),
        "pairs": rows, "counts": counts,
        "unpaired": [{"dir": u["dir"], "tag": u["tag"], "mode": u["mode"], "pc": u["origin"]}
                     for u in unpaired],
        "crossed_runs": crossed,
        "stopped_runs": find_stopped_runs(local, "this PC") + find_stopped_runs(other, "other PC"),
    }
    calibrations = find_calibrations(local, "this PC") + find_calibrations(other, "other PC")
    latest: dict[str, dict] = {}
    for item in calibrations:
        if item["direction"] and (item["direction"] not in latest
                                  or (item["started_utc"] or "") > (latest[item["direction"]]["started_utc"] or "")):
            latest[item["direction"]] = item
    report["calibration"] = {"latest": latest, "all": calibrations}
    report["cells"] = cell_table(rows)
    if report["cells"]:
        # A Phase 1 session: failures are results; the session needs review only for
        # attempts that could not be counted, crossed runs or runs without a partner.
        clean = (not crossed and not unpaired and not any(c["not_counted"] for c in report["cells"])
                 and not any(c.get("warning") for c in report["cells"]))
        report["outcome"] = "passed" if clean else "review"
    report["outcome"] = ("passed" if rows and set(counts) == {"passed"} and not crossed
                         else "review")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cross_check_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["cells"]:
        with open(out_dir / "phase1_cells.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["series", "arm", "condition", "load", "attempts", "counted", "successes", "failures",
                             "deadline_reached", "success_rate", "wilson_low", "wilson_high",
                             "median_delivery_s", "median_effective_snr_db", "not_counted"])
            for c in report["cells"]:
                writer.writerow([c["series"], c["arm"], c["condition"], c["load"], c["attempts"], c["counted"],
                                 c["successes"], c["failures"], c["deadline_reached"], c["success_rate"],
                                 c["wilson_95"][0], c["wilson_95"][1], c["median_delivery_s"],
                                 c["median_effective_snr_db"],
                                 len(c["not_counted"])])
    with open(out_dir / "cross_check_report.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["sender_start_utc", "tag", "outcome", "modem", "arm", "sender_run",
                         "receiver_run", "hash_matches", "pair_mismatches", "warnings"])
        for row in rows:
            writer.writerow([row["sender_start_utc"], row["sender_tag"], row["outcome"],
                             row["modem"], row["arm"], row["sender_dir"], row["receiver_dir"],
                             row["sender_receiver_hash_matches"],
                             " | ".join(row["pair_mismatches"]), " | ".join(row["warnings"])])
    return report


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--pair":
        local, other = Path(sys.argv[2]), Path(sys.argv[3])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = local / "cross_checks" / stamp
        report = pair_session(local, other, out_dir)
        print(json.dumps({k: report[k] for k in ("outcome", "counts", "unpaired", "crossed_runs")},
                         ensure_ascii=False, indent=2))
        for row in report["pairs"]:
            print(f"{row['sender_tag']:<12} {row['outcome']:<20} {row['sender_dir']} <-> {row['receiver_dir']}")
        print(f"\nReport saved to: {out_dir / 'cross_check_report.json'}")
        return 0 if report["outcome"] == "passed" else 1
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    em = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rx = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    result = cross_check(em, rx)
    output = Path(sys.argv[2]).with_name("cross_check.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
