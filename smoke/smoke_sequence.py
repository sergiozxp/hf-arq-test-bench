"""Run the whole MS-1.0 sequence on one PC: E0-1 (16 B, 1 KiB, 10 KiB), E0-2 V1 and
V2, E0-3 (same-session ACK), and H1 repetitions 1, 2 and 3, in that fixed order.

USAGE
  PC B (receiver), started first:  python smoke_sequence.py listen
  PC A (sender), started when B shows waiting_for_connection:
                                   python smoke_sequence.py send
  Options: --config smoke_config.toml, --from <step 1..9>, --gap-s <seconds>
  Phase 1 cell:  --campaign good|moderate|poor --load 200B|10KiB --attempts N --series P1|KIT|PILOT
  Phase 1 block: --block pilot|campaign|repeat   (every remaining attempt for the modem in use)

BLOCKS (alpha.5)
  A block has a fixed plan; each PC skips the attempts it has already completed for this
  arm and role, so RUN resumes a stopped block. PC B announces every attempt to the
  simulator (results/simulator/announce.json), which in Automatic mode takes the
  condition, attempt number and arm from it. A block stops after 3 attempts in a row
  that point to the bench, refuses to start if the modem binary or VARA's Drive level
  changed since the arm's first block (results/arm_settings.json), and ends with a
  results ZIP. "repeat" runs the missing and not-counted attempts of the last cross-check.

HOW THE TWO PCs STAY IN STEP
  The PCs share no network during the test, so each follows the same fixed script.
  The receiver starts listening for the next test as soon as one ends (each listen
  waits up to listen_timeout_s). The sender waits --gap-s seconds (default 45) after
  each test (60 s more after E0-3), so that the receiver has finished its bookkeeping
  and is listening again before the next CONNECT. Each PC stops at its first failure; the session
  cross-check rejects any pair of different tests, so a desynchronised step can
  never be counted as a pass.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import mercury_smoke_client
import smoke_gui_logic as logic
import vara_runtime as vara
from smoke_common import load_config, make_results_zip

DEFAULT_GAP_S = 45
# After a same-session ACK test the receiver ends later: the sender disconnects as soon
# as it has read the ACK, before the receiver's modem has had its own ACK frame
# confirmed, and the receiver then waits for the session to time out. Mercury 1.9.19
# reported DISCONNECTED to PC B 41 s after PC A's DISCONNECT (about 10 s otherwise),
# so the sender waits this much longer before the next test.
ACK_EXTRA_GAP_S = 60


def steps() -> list[tuple[str, int]]:
    return list(logic.SEQUENCE)


def step_args(mode: str, config: str, test_name: str, repetition: int) -> list[str]:
    command = logic.mercury_commands(logic.MERCURY_TESTS[test_name],
                                     "A" if mode == "send" else "B", repetition)[0]
    return [*command[1:], "--config", config]


def smoke_plan(mode: str, config: str) -> list[dict]:
    plan = []
    for name, repetition in steps():
        plan.append({"tag": logic.test_tag(name, repetition), "ack": bool(logic.MERCURY_TESTS[name].get("ack")),
                     "args": step_args(mode, config, name, repetition)})
    return plan


def cell_step(mode: str, config: str, series: str, condition: str, load: str, k: int) -> dict:
    """One Phase 1 attempt: it carries the ACK and the attempt deadline."""
    tag = logic.campaign_tag(condition, load, k, series)
    return {"tag": tag, "ack": True,
            "cell": {"series": series, "condition": condition, "load": load, "attempt": k},
            "args": [mode, "--tag", tag, "--phase", "p1", "--payload-size", str(logic.CAMPAIGN_LOADS[load]),
                     "--bw-when", "before", "--ack", "--deadline-s", str(logic.CAMPAIGN_DEADLINE_S[load]),
                     "--condition", condition, "--config", config]}


def campaign_plan(mode: str, config: str, condition: str, load: str, attempts: int,
                  series: str = "P1") -> list[dict]:
    """Phase 1 cell: attempts 1..N."""
    return [cell_step(mode, config, series, condition, load, k) for k in range(1, attempts + 1)]


# ------------------------------------------------------------------ blocks (alpha.5)
CELL_ORDER = {cell: i for i, cell in enumerate(
    [("KIT", "moderate", "200B"), *logic.BLOCK_CELLS["pilot"], *logic.BLOCK_CELLS["campaign"]])}
FAULT_LIMIT = 3          # consecutive attempts with a bench fault that stop a block
NO_CONNECT_IS_FAULT = ("good", "moderate")   # in poor, failing to connect can be the channel itself
ANNOUNCE_NAME = "announce.json"
STATUS_FRESH_S = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_runs(results: Path, mode: str, arm: str) -> list[dict]:
    """This PC's finished Phase 1 runs (result.json present) of this arm and role."""
    runs = []
    for path in sorted((results / "p1").glob("*/result.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # An attempt with an exception, an infrastructure or a procedure fault is not counted
        # by the cross-check, so it is not "done": the block runs it again on resume.
        faulty = (result.get("exception") or result.get("infrastructure_fault")
                  or mercury_smoke_client.has_setup_fault(result)
                  or bench_fault(result, result.get("condition") or "poor") is not None)
        if result.get("mode") == mode and result.get("arm") == arm and not faulty:
            stamp = path.parent.name.rsplit("_", 2)
            started = None
            if len(stamp) == 3:
                try:
                    started = datetime.strptime(stamp[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            runs.append({"tag": result.get("tag"), "dir": path.parent, "started": started, "result": result})
    return runs


def latest_cross_check(results: Path) -> tuple[Path | None, dict | None]:
    """The most recent cross-check. If it did not finish (no report), there is no fallback
    to an older one: on 2026-11-15 a failed cross-check made the repeat block read the
    previous one and find nothing to repeat."""
    folders = sorted(p for p in (results / "cross_checks").glob("*") if p.is_dir())
    if not folders:
        return None, None
    path = folders[-1] / "cross_check_report.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"the last cross-check ({folders[-1].name}) did not finish: run "
                         "'Cross-check with the other PC's ZIP' again and check that it ends with "
                         "the cell table") from None


def repeat_list(report: dict, arm: str) -> list[tuple[str, str, str, int]]:
    """Attempts of this arm that are missing or not counted in a cross-check report.

    Only cells with at least one paired attempt are considered; within them every attempt
    1..N (N = 10 for KIT and PILOT, 30 for P1) that has no counted pair is repeated with
    its own number, so with the same trace.
    """
    counted: dict[tuple, set] = {}
    for row in report.get("pairs") or []:
        if row.get("arm") != arm or "condition" not in row:
            continue
        key = (row.get("series", "P1"), row["condition"], row["load"])
        counted.setdefault(key, set())
        if row.get("outcome") in ("passed", "failed"):
            counted[key].add(row.get("attempt"))
    out = []
    for key in sorted(counted, key=lambda c: CELL_ORDER.get(c, 99)):
        for k in range(1, logic.SERIES_ATTEMPTS.get(key[0], 30) + 1):
            if k not in counted[key]:
                out.append((*key, k))
    return out


def block_plan(mode: str, config: str, kind: str, results: Path, arm: str,
               start: int = 0) -> tuple[list[dict], int, str]:
    """The block's remaining attempts on this PC, the number already done, and a note.

    start > 0 (pilot and campaign): every attempt from that plan position on, without
    skipping, so that both PCs restart at the same attempt after one of them lost it (for
    example a modem crash on PC A while PC B recorded the attempt as a failure). An attempt
    run again counts once in the cross-check.
    """
    if kind == "repeat":
        path, report = latest_cross_check(results)
        if report is None:
            raise ValueError("no cross-check yet: exchange the ZIPs and cross-check on this PC first")
        created = datetime.fromisoformat(report["created_utc"])
        if not any(row.get("arm") == arm and "condition" in row for row in report.get("pairs") or []):
            raise ValueError(f"the last cross-check ({path.parent.name}) has no Phase 1 attempts of "
                             f"{arm}: cross-check again with the other PC's latest ZIP")
        wanted = repeat_list(report, arm)
        plan = [cell_step(mode, config, *item) for item in wanted]
        # resume: an attempt already run again after this cross-check is not run a third time
        again = {r["tag"] for r in local_runs(results, mode, arm) if r["started"] and r["started"] >= created}
        remaining = [s for s in plan if s["tag"] not in again]
        return remaining, len(plan) - len(remaining), f"from the cross-check {path.parent.name}"
    plan = [cell_step(mode, config, series, cond, load, k)
            for series, cond, load in logic.BLOCK_CELLS[kind]
            for k in range(1, logic.SERIES_ATTEMPTS[series] + 1)]
    if start:
        if not 1 <= start <= len(plan):
            raise ValueError(f"the start position must be between 1 and {len(plan)}")
        return plan[start - 1:], 0, f"from position {start} ({plan[start - 1]['tag']}), without skipping"
    done = {r["tag"] for r in local_runs(results, mode, arm)}
    remaining = [s for s in plan if s["tag"] not in done]
    return remaining, len(plan) - len(remaining), ""


def write_json(path: Path, record: dict) -> None:
    """Atomic write; retried while another process (the simulator) reads the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    for _ in range(50):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05)
    tmp.replace(path)


def announce(results: Path, step: dict, arm: str, state: str, entry_id: str) -> None:
    """PC B tells the simulator (same PC) which attempt comes next, and when it has ended."""
    write_json(results / "simulator" / ANNOUNCE_NAME,
               {"id": entry_id, "state": state, "tag": step["tag"], "arm": arm, **step["cell"],
                "written_utc": utc_now().isoformat(timespec="milliseconds")})


def simulator_problem(results: Path, arm: str) -> str | None:
    """PC B, before a block: the simulator must be running in Automatic mode, with references."""
    folders = sorted((results / "simulator").glob("*/status.json"))
    if not folders:
        return "the channel simulator is not running: start it in Automatic mode (Tools > Channel simulator)"
    try:
        status = json.loads(folders[-1].read_text(encoding="utf-8"))
        age = (utc_now() - datetime.fromisoformat(status["t_utc"])).total_seconds()
    except (OSError, ValueError, KeyError):
        return "cannot read the simulator status: restart the simulator in Automatic mode"
    if age > STATUS_FRESH_S:
        return "the channel simulator is not running: start it in Automatic mode (Tools > Channel simulator)"
    if status.get("mode") != "auto":
        return f"the simulator runs in '{status.get('mode')}' mode: stop it and start it in Automatic mode"
    try:
        power = json.loads((results / "simulator" / "modem_power.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        power = {}
    if arm not in power:
        return f"no RMS power reference for {arm}: run its Transparent sequence and save it first"
    return None


def arm_settings(cfg: dict) -> dict:
    """What must not change during the campaign: the modem binary and, for VARA, its Drive level."""
    modem = cfg.get("modem", {}).get("name")
    settings = {"modem_binary_sha256": mercury_smoke_client.modem_binary(cfg, modem)}
    if modem == "vara":
        ini = cfg.get("evidence", {}).get("vara_ini")
        try:
            settings["drive_level_db"] = vara.read_ini(ini)["drive_level_db"] if ini else None
        except (OSError, ValueError):
            settings["drive_level_db"] = None
    return settings


def check_lock(results: Path, cfg: dict, arm: str) -> tuple[str | None, dict]:
    """The first block of an arm records its settings; later blocks must find the same ones."""
    path = results / "arm_settings.json"
    try:
        locks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        locks = {}
    now = arm_settings(cfg)
    if arm not in locks:
        locks[arm] = {**now, "recorded_utc": utc_now().isoformat(timespec="seconds")}
        write_json(path, locks)
        return None, locks[arm]
    changed = [f"{k} was {locks[arm].get(k)}, now {now.get(k)}" for k in now
               if locks[arm].get(k) is not None and now.get(k) != locks[arm].get(k)]
    if changed:
        return (f"{arm} changed since its first block ({'; '.join(changed)}). Restore it; the campaign "
                f"needs the same binary and Drive throughout (recorded in {path})."), locks[arm]
    return None, locks[arm]


def latest_result(results: Path, tag: str, after: datetime) -> dict | None:
    for folder in sorted((results / "p1").glob(f"{tag}_*"), reverse=True):
        path = folder / "result.json"
        if path.is_file():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            stamp = folder.name.rsplit("_", 2)[1]
            if datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) >= after.replace(microsecond=0):
                return result
        return None
    return None


def bench_fault(result: dict | None, condition: str) -> str | None:
    """Why an attempt points to the bench rather than the channel, or None."""
    if result is None:
        return "the attempt ended without a result (script error or modem not reachable)"
    if result.get("exception"):
        return "script error or modem not reachable: " + (result.get("failures") or ["?"])[-1][:120]
    if result.get("infrastructure_fault"):
        return "infrastructure fault reported by the modem"
    if mercury_smoke_client.has_setup_fault(result):
        return "procedure fault (" + (result.get("procedure_fault_detail") or "setup command rejected")[:120] + ")"
    failures = " ".join(result.get("failures") or [])
    if condition in NO_CONNECT_IS_FAULT and "no CONNECTED event" in failures:
        return f"no connection in the {condition} condition"
    return None


def make_zip(results: Path, role: str, arm: str, kind: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", arm.replace("unlicensed", "free"))
    name = Path.cwd() / f"results_{role}_{slug}_{kind}_{utc_now():%Y%m%d_%H%M%S}"
    return make_results_zip(results, name)


def run_step(args_list: list[str]) -> int:
    old = sys.argv
    try:
        sys.argv = ["mercury_smoke_client.py", *args_list]
        return mercury_smoke_client.main()
    finally:
        sys.argv = old


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the full MS-1.0 smoke sequence, a Phase 1 cell or a block, on one PC.")
    ap.add_argument("mode", choices=["send", "listen"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--from", dest="start", type=int, default=1, help="first step or attempt")
    ap.add_argument("--gap-s", type=float, default=DEFAULT_GAP_S,
                    help="sender only: seconds to wait between tests")
    ap.add_argument("--ack-extra-s", type=float, default=ACK_EXTRA_GAP_S,
                    help="sender only: extra seconds to wait after a same-session ACK test")
    ap.add_argument("--campaign", choices=list(logic.CAMPAIGN_CONDITIONS), default=None,
                    help="Phase 1: run one cell in this channel condition")
    ap.add_argument("--load", choices=list(logic.CAMPAIGN_LOADS), default="200B")
    ap.add_argument("--attempts", type=int, default=30)
    ap.add_argument("--series", choices=["P1", "KIT", "PILOT", "VERIFY"], default="P1",
                    help="P1 campaign, KIT minimum replication kit, PILOT exploratory pilot, "
                         "VERIFY verification run (reported apart)")
    ap.add_argument("--block", choices=["pilot", "campaign", "repeat"], default=None,
                    help="every remaining attempt of a stage for the modem in use (resumes by itself)")
    ap.add_argument("--block-from", type=int, default=0,
                    help="pilot/campaign block: run every attempt from this plan position on (no skipping), "
                         "with the same position on both PCs")
    ap.add_argument("--no-zip", action="store_true", help="block: do not create the results ZIP at the end")
    args = ap.parse_args()
    cfg = load_config(args.config)
    results = Path(cfg.get("general", {}).get("results_dir", "results"))
    arm = mercury_smoke_client.modem_identity(cfg)[2]
    campaign = args.campaign is not None or args.block is not None
    started = utc_now()
    report = {"mode": args.mode, "started_utc": started.isoformat(timespec="seconds"),
              "gap_s": args.gap_s if args.mode == "send" else 0, "from_step": args.start, "steps": []}
    out = results / "sequences"
    out.mkdir(parents=True, exist_ok=True)

    def save(kind: str, quiet: bool = False) -> Path:
        path = out / f"{kind}_{args.mode}_{started:%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not quiet:
            print(f"Sequence saved to: {path}", flush=True)
        return path

    if args.block:
        report["block"] = {"kind": args.block, "arm": arm}
        try:
            plan, skipped, note = block_plan(args.mode, args.config, args.block, results, arm,
                                             args.block_from)
        except ValueError as error:
            plan, skipped, note = [], 0, ""
            report.update(outcome="stopped", stop_reason=f"{error}.")
        report["skipped_done"] = skipped
        if note:
            report["block"]["source"] = note
        problem, lock = check_lock(results, cfg, arm) if "outcome" not in report else (None, {})
        report["block"]["settings"] = lock
        if problem:
            report.update(outcome="stopped", stop_reason=problem)
        elif "outcome" not in report and args.mode == "listen" and plan:
            problem = simulator_problem(results, arm)
            if problem:
                report.update(outcome="stopped", stop_reason=f"{problem[0].upper()}{problem[1:]}.")
        if "outcome" in report:
            print(f"\nBLOCK NOT STARTED: {report['stop_reason']}", flush=True)
            report.update(completed_steps=0, planned_steps=len(plan))
            save("block")
            return 2
        if not plan:
            report.update(outcome="nothing_to_do", completed_steps=0, planned_steps=0)
            print(f"\nBLOCK {args.block.upper()}: nothing left to run for {arm} ({skipped} attempt(s) already done).",
                  flush=True)
            save("block")
            return 0
        print(f"BLOCK {args.block.upper()} for {arm}: {len(plan)} attempt(s) to run, {skipped} already done. "
              f"First: {plan[0]['tag']}. PC A and PC B must show the same first attempt.", flush=True)
        start = 1
    else:
        plan = (campaign_plan(args.mode, args.config, args.campaign, args.load, args.attempts, args.series)
                if campaign else smoke_plan(args.mode, args.config))
        start = args.start
        if not 1 <= start <= len(plan):
            print(f"--from must be between 1 and {len(plan)}")
            return 2
    if args.campaign is not None:
        report["campaign"] = {"series": args.series, "condition": args.campaign, "load": args.load,
                              "attempts": args.attempts,
                              "deadline_s": logic.CAMPAIGN_DEADLINE_S[args.load]}
    exit_code = 0
    faults: list[str] = []
    for number, step in enumerate(plan, start=1):
        if number < start:
            continue
        tag = step["tag"]
        if args.mode == "send" and number > start:
            wait = args.gap_s + (args.ack_extra_s if plan[number - 2]["ack"] else 0)
            print(f"\nWaiting {wait:.0f} s so that PC B is listening for {tag}…", flush=True)
            time.sleep(wait)
        print(f"\n=== STEP {number}/{len(plan)}: {tag} ({'SENDER' if args.mode == 'send' else 'RECEIVER'}) ===",
              flush=True)
        entry_id = uuid.uuid4().hex[:12]
        if campaign and args.mode == "listen":
            announce(results, step, arm, "listening", entry_id)
        t0, step_start = time.monotonic(), utc_now()
        code = run_step(step["args"])
        if campaign and args.mode == "listen":
            announce(results, step, arm, "finished", entry_id)
            time.sleep(1.5)    # long enough for the simulator to see "finished" before the next entry
        record = {"step": number, "tag": tag, "exit_code": code, "duration_s": round(time.monotonic() - t0, 1)}
        report["steps"].append(record)
        if code != 0 and not campaign:
            print(f"\nSEQUENCE STOPPED at step {number} ({tag}): this test did not pass on this PC.",
                  flush=True)
            exit_code = code
            break
        # In a campaign a failed attempt is a result, not a reason to stop; a block stops
        # only when several attempts in a row point to the bench itself.
        if args.block:
            fault = bench_fault(latest_result(results, tag, step_start), step["cell"]["condition"])
            if fault:
                record["bench_fault"] = fault
                faults.append(f"{tag}: {fault}")
            else:
                faults = []
            if len(faults) >= FAULT_LIMIT:
                report.update(outcome="stopped", stop_reason=(
                    f"{FAULT_LIMIT} attempts in a row point to the bench, not the channel ("
                    + "; ".join(faults) + ")."))
                print(f"\nBLOCK STOPPED: {report['stop_reason']}", flush=True)
                exit_code = 3
                break
    done = [s for s in report["steps"] if s["exit_code"] == 0]
    report["completed_steps"] = len(done)
    report["planned_steps"] = len(plan) - start + 1
    report.setdefault("outcome", "completed" if exit_code == 0 else "stopped")
    if args.block and not args.no_zip:
        if args.mode == "listen":
            time.sleep(3)      # the simulator closes the last attempt on PC B's "finished"
        report["zip"] = "(this ZIP)"
        save("block", quiet=True)   # the block's own report goes into its ZIP
        report["zip"] = make_zip(results, "A" if args.mode == "send" else "B", arm, args.block)
        print(f"Results ZIP: {report['zip']}", flush=True)
    if args.block:
        print(f"\nBLOCK {args.block.upper()} {report['outcome'].upper()}: {len(report['steps'])} attempt(s) run on "
              f"this PC, {report['skipped_done']} already done before. Next: exchange the ZIPs and cross-check on "
              "both PCs.", flush=True)
        save("block")
    elif campaign:
        print(f"\nCELL FINISHED: {len(report['steps'])} attempt(s) run, {len(done)} ended without failure on "
              "this PC. Next: cross-check with the other PC's ZIP for the cell report.", flush=True)
        save("campaign")
    else:
        print(f"\nSEQUENCE {report['outcome'].upper()}: {len(done)} of {report['planned_steps']} test(s) "
              f"completed on this PC. Next: cross-check the session with the other PC's ZIP.", flush=True)
        save("sequence")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
