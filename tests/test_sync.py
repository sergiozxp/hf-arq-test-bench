"""PC A and PC B stay in step (0.2.0-alpha.8).

2026-11-16, VARA HF licensed: PC B kept listening after an attempt had ended for PC A (a
link lost in the middle of a transfer, a call never answered, an ACK whose BUFFER 0 never
came) and took PC A's next attempt as the current one; the PCs went out of step. In Phase 1
each side now ends the attempt when the session ends, PC B stops listening when the
simulator shows that PC A called and then stopped calling, and the cross-check refuses pairs
whose runs were not the same session. Checked end to end against a mock modem.
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "smoke"))
sys.path.insert(0, str(HERE))
import smoke_common
import smoke_cross_check as cc
import smoke_sequence as seq
from test_launcher import CONFIG


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


temporary = Path(tempfile.mkdtemp(prefix="hf_arq_sync_"))
configs = {}
for local, peer, command_port, data_port in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
    (temporary / f"results_{local}").mkdir()
    configs[local] = temporary / f"config_{local}.toml"
    configs[local].write_text(CONFIG.format(results=temporary / f"results_{local}", command_port=command_port,
                                            data_port=data_port, local=local, peer=peer), encoding="utf-8")
client = [sys.executable, str(ROOT / "smoke" / "mercury_smoke_client.py")]


def pair(tag: str, deadline: int = 600, before_send=None) -> tuple[dict, dict, float, float]:
    extra = ["--ack", "--phase", "p1", "--deadline-s", str(deadline), "--condition", "poor"]
    t0 = time.monotonic()
    rx = subprocess.Popen(client + ["listen", "--config", str(configs["B"]), "--tag", tag, "--payload-size", "200",
                                    *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    lines: list[str] = []
    reader = threading.Thread(target=lambda: [lines.append(l) for l in rx.stdout], daemon=True)
    reader.start()
    limit = time.time() + 120
    while time.time() < limit and not any("waiting_for_connection" in l for l in lines):
        time.sleep(0.3)
    if before_send:
        before_send()
    t1 = time.monotonic()
    subprocess.run(client + ["send", "--config", str(configs["A"]), "--tag", tag, "--payload-size", "200", *extra],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    send_s = time.monotonic() - t1
    rx.wait(timeout=900)
    listen_s = time.monotonic() - t0
    reader.join(timeout=10)
    a = json.loads(Path(glob.glob(str(temporary / "results_A" / "p1" / f"{tag}_*" / "result.json"))[0]).read_text())
    b = json.loads(Path(glob.glob(str(temporary / "results_B" / "p1" / f"{tag}_*" / "result.json"))[0]).read_text())
    return a, b, send_s, listen_s


def mock(mode: str):
    process = subprocess.Popen([sys.executable, str(HERE / "mock_mercury.py"), mode],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    return process


# 1. Interruptible reads.
class FakeLog:
    dir = temporary

    def log(self, *a, **k):
        pass


capture = smoke_common.TcpCapture.__new__(smoke_common.TcpCapture)
capture.cond, capture.buf, capture.closed, capture.error = threading.Condition(), bytearray(), False, None
capture.stop_check = lambda: "the session ended (DISCONNECTED)"
t = time.monotonic()
try:
    capture.read_exact(10, 600)
    check(False, "a read stops when the session ends")
except ConnectionError as error:
    check("session ended" in str(error) and time.monotonic() - t < 2, "a read stops when the session ends")

# 2. A link lost in the middle of the transfer: both sides end at once, not at the deadline.
process = mock("drop_session")
try:
    a, b, send_s, listen_s = pair("P1-poor-200B-01")
    check(send_s < 60 and listen_s < 200, f"link lost: PC A ended after {send_s:.0f} s and PC B after "
          f"{listen_s:.0f} s (deadline 600 s)")
    check(not a.get("sender_counted_success") and not b.get("delivered_intact"), "link lost: a failure on both sides")
    check(not a.get("procedure_fault") and not b.get("procedure_fault"), "link lost is a result, not a procedure fault")
    check(cc.cross_check(a, b)["outcome"] == "failed", "link lost: the pair is a counted failure")
finally:
    process.terminate()
    process.wait(timeout=10)

# 3. A call that is never answered: PC A ends when its modem gives up; PC B stops listening
#    when the simulator (Automatic mode) shows that PC A called and then went silent.
status_dir = temporary / "results_B" / "simulator" / "29991231T000000Z_auto"
status_dir.mkdir(parents=True)
stop = threading.Event()


def simulator(tag: str) -> None:
    while not stop.is_set():
        (status_dir / "status.json").write_text(json.dumps({
            "t_utc": datetime.now(timezone.utc).isoformat(), "mode": "auto", "tag": tag,
            "seconds_since_audio": 75.0}), encoding="utf-8")
        time.sleep(0.5)


process = mock("no_answer")
try:
    a, b, send_s, listen_s = pair("P1-poor-200B-02", before_send=lambda: threading.Thread(
        target=simulator, args=("P1-poor-200B-02",), daemon=True).start())
    check(send_s < 60, f"no answer: PC A ended {send_s:.0f} s after calling (deadline 600 s)")
    check(listen_s < 200 and b.get("caller_gave_up") is True, f"no answer: PC B stopped listening after "
          f"{listen_s:.0f} s because PC A had stopped calling")
    check("no CONNECTED event was observed" in " ".join(a.get("failures") or [])
          and cc.cross_check(a, b)["outcome"] == "failed", "no answer: a counted failure")
finally:
    stop.set()
    process.terminate()
    process.wait(timeout=10)

# 4. The cross-check refuses runs that were not the same session.
def fake_run(folder: Path, connected: datetime, end: datetime, start: datetime, failures=()) -> dict:
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text(
        json.dumps({"t_utc": start.isoformat(), "event": "start"}) + "\n"
        + json.dumps({"t_utc": connected.isoformat(), "event": "line_received", "text": "CONNECTED X Y 500"}) + "\n"
        + json.dumps({"t_utc": end.isoformat(), "event": "end"}) + "\n", encoding="utf-8")
    (folder / "result.json").write_text("{}", encoding="utf-8")
    return {"path": folder / "result.json", "start": start, "result": {"failures": list(failures)}}


now = datetime.now(timezone.utc).replace(microsecond=0)
sender = fake_run(temporary / "x" / "s", now + timedelta(seconds=10), now + timedelta(seconds=600), now)
same = fake_run(temporary / "x" / "r1", now + timedelta(seconds=11), now + timedelta(seconds=601), now - timedelta(seconds=100))
later = fake_run(temporary / "x" / "r2", now + timedelta(seconds=700), now + timedelta(seconds=760), now + timedelta(seconds=650))
check(not cc.step_reasons(sender, same, {"start": now + timedelta(seconds=5)}), "same session: accepted")
check(any("different sessions" in r for r in cc.step_reasons(sender, later, None)),
      "PC B one attempt behind: refused (different sessions)")
check(any("after PC A's run had ended" in r for r in cc.step_reasons(sender, same, {"start": now + timedelta(seconds=700)})),
      "a simulator attempt after PC A's run: refused")
absorbed = fake_run(temporary / "x" / "r3", now + timedelta(seconds=11), now + timedelta(seconds=601), now,
                    failures=["216 bytes on the data port outside the CONNECTED session"])
check(any("another session" in r for r in cc.step_reasons(sender, absorbed, None)),
      "PC B's run that also received another session: refused")
check(seq.bench_fault({"failures": ["PROCEDURE: 2 sessions in one run (PC A's next attempt reached this run)"]},
                      "poor") is not None, "guard: two sessions in one run is a bench fault")
gave_up = fake_run(temporary / "x" / "r4", now + timedelta(seconds=11), now + timedelta(seconds=20), now)
gave_up["result"] = {"failures": ["no CONNECTED event was observed (PC A's call was heard, then PC A stopped calling)"],
                     "caller_gave_up": True}
check(any("stopped listening before PC A's call" in r for r in cc.step_reasons(sender, gave_up, None)),
      "PC B gave up while PC A's call then connected: refused (not a modem failure)")
events = [json.loads(l) for l in Path(glob.glob(str(temporary / "results_B" / "p1" / "P1-poor-200B-02_*" / "events.jsonl"))[0]).read_text(encoding="utf-8").splitlines() if l.strip()]
sent = [e.get("label") for e in events if e.get("event") == "tx"]
check("LISTEN OFF" in sent and ("DISCONNECT" not in sent or sent.index("LISTEN OFF") < sent.index("DISCONNECT")),
      "PC B without a session stops answering calls (LISTEN OFF) before its cleanup DISCONNECT")
print("ALL SYNC TESTS PASSED")
