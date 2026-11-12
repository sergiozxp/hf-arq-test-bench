"""Phase 1 success without BUFFER 0 (PILOT-poor-200B-10, Mercury, 2026-11-12).

B verified the payload and A verified B's ACK, but Mercury never reported BUFFER 0 to A.
By the Phase 1 definition that is a success; BUFFER 0 is a diagnostic warning. This test
checks (1) the real result files of that attempt, and (2) the client itself, end to end,
against a mock modem that never reports BUFFER 0 to A. The smoke E0-3 test (no channel
condition) still requires BUFFER 0.
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "smoke"))
sys.path.insert(0, str(HERE))
import smoke_cross_check as cc
from test_launcher import CONFIG


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


# 1. The real evidence of the attempt.
sender = json.loads((HERE / "fixtures" / "mercury_pilot_poor_10_sender.json").read_text(encoding="utf-8"))
receiver = json.loads((HERE / "fixtures" / "mercury_pilot_poor_10_receiver.json").read_text(encoding="utf-8"))
check(sender.get("arq_confirmed") is not True and sender.get("ack_verified") is True
      and receiver.get("delivered_intact") is True, "fixture: no BUFFER 0, verified ACK, intact payload at B")
check(cc.cross_check(sender, receiver)["outcome"] == "passed", "real attempt PILOT-poor-200B-10 is a success")

# 2. The client, end to end, with a modem that never reports BUFFER 0 to A.
temporary = Path(tempfile.mkdtemp(prefix="hf_arq_nob0_"))
configs = {}
for local, peer, command_port, data_port in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
    (temporary / f"results_{local}").mkdir()
    configs[local] = temporary / f"config_{local}.toml"
    configs[local].write_text(CONFIG.format(results=temporary / f"results_{local}", command_port=command_port,
                                            data_port=data_port, local=local, peer=peer), encoding="utf-8")
client = [sys.executable, str(ROOT / "smoke" / "mercury_smoke_client.py")]
mock = subprocess.Popen([sys.executable, str(HERE / "mock_mercury.py"), "no_buffer0"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pair(tag: str, phase1: bool) -> tuple[dict, dict]:
    extra = ["--ack"] + (["--phase", "p1", "--deadline-s", "600", "--condition", "poor"] if phase1 else [])
    rx = subprocess.Popen(client + ["listen", "--config", str(configs["B"]), "--tag", tag, "--payload-size", "200",
                                    *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    lines: list[str] = []
    reader = threading.Thread(target=lambda: [lines.append(l) for l in rx.stdout], daemon=True)
    reader.start()
    deadline = time.time() + 120
    while time.time() < deadline and not any("waiting_for_connection" in l for l in lines):
        time.sleep(0.3)
    subprocess.run(client + ["send", "--config", str(configs["A"]), "--tag", tag, "--payload-size", "200", *extra],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    rx.wait(timeout=600)
    reader.join(timeout=10)
    folder = "p1" if phase1 else "e0"
    a = json.loads(Path(glob.glob(str(temporary / "results_A" / folder / f"{tag}_*" / "result.json"))[0]).read_text())
    b = json.loads(Path(glob.glob(str(temporary / "results_B" / folder / f"{tag}_*" / "result.json"))[0]).read_text())
    return a, b


try:
    time.sleep(1.5)
    a, b = pair("PILOT-poor-200B-10", phase1=True)
    check(a.get("ack_verified") is True and not a.get("arq_confirmed"), "mock: A verifies the ACK, no BUFFER 0")
    check(a.get("sender_completed") is True and not a.get("failures"), "Phase 1: A records no failure")
    check(any("BUFFER 0" in w for w in a.get("protocol_warnings") or []), "Phase 1: missing BUFFER 0 is recorded as a protocol warning")
    check(a.get("delivery_time_s") is not None, "Phase 1: delivery time measured to the verified ACK")
    check(cc.cross_check(a, b)["outcome"] == "passed", "Phase 1: the pair is a success")
    a, b = pair("E0-3_ACK_TEST", phase1=False)
    check(any("BUFFER 0" in f for f in a.get("failures") or []) and cc.cross_check(a, b)["outcome"] == "failed",
          "smoke E0-3 (no channel condition) still requires BUFFER 0")
finally:
    mock.terminate()
print("ALL NO-BUFFER-0 TESTS PASSED")
