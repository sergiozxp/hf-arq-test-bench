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
  each test, so that the receiver has finished its bookkeeping and is listening
  again before the next CONNECT. Each PC stops at its first failure; the session
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


def steps() -> list[tuple[str, int]]:
    return list(logic.SEQUENCE)


def step_args(mode: str, config: str, test_name: str, repetition: int) -> list[str]:
    command = logic.mercury_commands(logic.MERCURY_TESTS[test_name],
                                     "A" if mode == "send" else "B", repetition)[0]
    return [*command[1:], "--config", config]


def run_step(args_list: list[str]) -> int:
    old = sys.argv
    try:
        sys.argv = ["mercury_smoke_client.py", *args_list]
        return mercury_smoke_client.main()
    finally:
        sys.argv = old


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the full MS-1.0 smoke sequence on one PC.")
    ap.add_argument("mode", choices=["send", "listen"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--from", dest="start", type=int, default=1, help="first step (1-9)")
    ap.add_argument("--gap-s", type=float, default=DEFAULT_GAP_S,
                    help="sender only: seconds to wait between tests")
    args = ap.parse_args()
    plan = steps()
    if not 1 <= args.start <= len(plan):
        print(f"--from must be between 1 and {len(plan)}")
        return 2
    cfg = load_config(args.config)
    started = datetime.now(timezone.utc)
    report = {"mode": args.mode, "started_utc": started.isoformat(timespec="seconds"),
              "gap_s": args.gap_s if args.mode == "send" else 0, "from_step": args.start,
              "steps": []}
    exit_code = 0
    for number, (name, repetition) in enumerate(plan, start=1):
        if number < args.start:
            continue
        tag = logic.test_tag(name, repetition)
        if args.mode == "send" and number > args.start:
            print(f"\nWaiting {args.gap_s:.0f} s so that PC B is listening for {tag}…", flush=True)
            time.sleep(args.gap_s)
        print(f"\n=== STEP {number}/{len(plan)}: {tag} ({'SENDER' if args.mode == 'send' else 'RECEIVER'}) ===",
              flush=True)
        t0 = time.monotonic()
        code = run_step(step_args(args.mode, args.config, name, repetition))
        report["steps"].append({"step": number, "tag": tag, "exit_code": code,
                                "duration_s": round(time.monotonic() - t0, 1)})
        if code != 0:
            print(f"\nSEQUENCE STOPPED at step {number} ({tag}): this test did not pass on this PC.",
                  flush=True)
            exit_code = code
            break
    done = [s for s in report["steps"] if s["exit_code"] == 0]
    report["completed_steps"] = len(done)
    report["planned_steps"] = len(plan) - args.start + 1
    report["outcome"] = "completed" if exit_code == 0 else "stopped"
    out = Path(cfg.get("general", {}).get("results_dir", "results")) / "sequences"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"sequence_{args.mode}_{started:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSEQUENCE {report['outcome'].upper()}: {len(done)} of {report['planned_steps']} test(s) "
          f"completed on this PC. Next: cross-check the session with the other PC's ZIP.", flush=True)
    print(f"Sequence saved to: {path}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
