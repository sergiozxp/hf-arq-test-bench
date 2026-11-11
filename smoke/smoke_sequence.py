"""Run the whole MS-1.0 sequence on one PC: E0-1 (16 B, 1 KiB, 10 KiB), E0-2 V1 and
V2, E0-3 (same-session ACK), and H1 repetitions 1, 2 and 3, in that fixed order.

USAGE
  PC B (receiver), started first:  python smoke_sequence.py listen
  PC A (sender), started when B shows waiting_for_connection:
                                   python smoke_sequence.py send
  Options: --config smoke_config.toml, --from <step 1..9>, --gap-s <seconds>

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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import mercury_smoke_client
import smoke_gui_logic as logic
from smoke_common import load_config

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


def campaign_plan(mode: str, config: str, condition: str, load: str, attempts: int,
                  series: str = "P1") -> list[dict]:
    """Phase 1 cell: every attempt carries the ACK and the attempt deadline."""
    plan = []
    for k in range(1, attempts + 1):
        tag = logic.campaign_tag(condition, load, k, series)
        plan.append({"tag": tag, "ack": True, "args": [
            mode, "--tag", tag, "--phase", "p1", "--payload-size", str(logic.CAMPAIGN_LOADS[load]),
            "--bw-when", "before", "--ack", "--deadline-s", str(logic.CAMPAIGN_DEADLINE_S[load]),
            "--condition", condition, "--config", config]})
    return plan


def run_step(args_list: list[str]) -> int:
    old = sys.argv
    try:
        sys.argv = ["mercury_smoke_client.py", *args_list]
        return mercury_smoke_client.main()
    finally:
        sys.argv = old


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the full MS-1.0 smoke sequence, or a Phase 1 cell, on one PC.")
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
    ap.add_argument("--series", choices=["P1", "KIT", "PILOT"], default="P1",
                    help="P1 campaign, KIT minimum replication kit, PILOT exploratory pilot")
    args = ap.parse_args()
    campaign = args.campaign is not None
    plan = (campaign_plan(args.mode, args.config, args.campaign, args.load, args.attempts, args.series) if campaign
            else smoke_plan(args.mode, args.config))
    if not 1 <= args.start <= len(plan):
        print(f"--from must be between 1 and {len(plan)}")
        return 2
    cfg = load_config(args.config)
    started = datetime.now(timezone.utc)
    report = {"mode": args.mode, "started_utc": started.isoformat(timespec="seconds"),
              "gap_s": args.gap_s if args.mode == "send" else 0, "from_step": args.start, "steps": []}
    if campaign:
        report["campaign"] = {"series": args.series, "condition": args.campaign, "load": args.load,
                              "attempts": args.attempts,
                              "deadline_s": logic.CAMPAIGN_DEADLINE_S[args.load]}
    exit_code = 0
    for number, step in enumerate(plan, start=1):
        if number < args.start:
            continue
        tag = step["tag"]
        if args.mode == "send" and number > args.start:
            wait = args.gap_s + (args.ack_extra_s if plan[number - 2]["ack"] else 0)
            print(f"\nWaiting {wait:.0f} s so that PC B is listening for {tag}…", flush=True)
            time.sleep(wait)
        print(f"\n=== STEP {number}/{len(plan)}: {tag} ({'SENDER' if args.mode == 'send' else 'RECEIVER'}) ===",
              flush=True)
        t0 = time.monotonic()
        code = run_step(step["args"])
        report["steps"].append({"step": number, "tag": tag, "exit_code": code,
                                "duration_s": round(time.monotonic() - t0, 1)})
        if code != 0 and not campaign:
            print(f"\nSEQUENCE STOPPED at step {number} ({tag}): this test did not pass on this PC.",
                  flush=True)
            exit_code = code
            break
        # In a campaign a failed attempt is a result, not a reason to stop.
    done = [s for s in report["steps"] if s["exit_code"] == 0]
    report["completed_steps"] = len(done)
    report["planned_steps"] = len(plan) - args.start + 1
    report["outcome"] = "completed" if exit_code == 0 else "stopped"
    out = Path(cfg.get("general", {}).get("results_dir", "results")) / "sequences"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{'campaign' if campaign else 'sequence'}_{args.mode}_{started:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if campaign:
        print(f"\nCELL FINISHED: {len(report['steps'])} attempt(s) run, {len(done)} ended without failure on "
              "this PC. Next: cross-check with the other PC's ZIP for the cell report.", flush=True)
    else:
        print(f"\nSEQUENCE {report['outcome'].upper()}: {len(done)} of {report['planned_steps']} test(s) "
              f"completed on this PC. Next: cross-check the session with the other PC's ZIP.", flush=True)
    print(f"Sequence saved to: {path}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
