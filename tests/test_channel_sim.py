"""Simulator runtime without audio hardware: reference capture, attempts, validity."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "smoke"))
import channel_model as cm
import channel_sim as sim


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


FS, B = cm.FS, cm.BLOCK
rng = np.random.default_rng(3)


def seconds(s: float) -> int:
    return int(round(s * FS / B)) * B


def tone(s: float, peak_dbfs: float) -> np.ndarray:
    n = np.arange(seconds(s))
    return 10 ** (peak_dbfs / 20) * np.sin(2 * np.pi * 1500 * n / FS)


def burst(s: float, peak_dbfs: float) -> np.ndarray:
    """A modem-like burst: band-limited noise scaled to the given peak."""
    x = cm.StreamFIR(cm.noise_bandpass()).process(rng.standard_normal(seconds(s)))
    return x / np.max(np.abs(x)) * 10 ** (peak_dbfs / 20)


def silence(s: float) -> np.ndarray:
    return np.zeros(seconds(s))


def feed(core, left, right, status_at=None):
    n = max(len(left), len(right))
    left, right = np.pad(left, (0, n - len(left))), np.pad(right, (0, n - len(right)))
    out = []
    for i in range(0, n, B):
        status = status_at.get(i // B) if status_at else None
        out.append(core.process(np.stack([left[i:i + B], right[i:i + B]], axis=1), status))
    return np.concatenate(out)


# ---------------------------------------------------------------- reference from the CAL tone
events = []
core = sim.SimulatorCore("transparent", None, emit=events.append)
left = np.concatenate([silence(2), tone(6, -6.7), silence(20)])
right = np.concatenate([silence(16), tone(6, -6.5), silence(4)])
out = feed(core, left, right)
core.finish()
refs = core.references_found
check(set(refs) == {"A->B", "B->A"}, "the calibration tone gives a reference for both directions")
check(abs(refs["A->B"]["tone_peak_dbfs"] + 6.7) < 0.05 and abs(refs["B->A"]["tone_peak_dbfs"] + 6.5) < 0.05,
      f"reference peaks {refs['A->B']['tone_peak_dbfs']} / {refs['B->A']['tone_peak_dbfs']} dBFS")
check(np.allclose(out[:, 0], np.clip(left, -1, 1) * 10 ** (cm.OUTPUT_GAIN_DB / 20)),
      "transparent output = input - 12 dB")
short = sim.SimulatorCore("transparent", None, emit=lambda e: None)
feed(short, np.concatenate([tone(2, -6), silence(1)]), silence(3))
short.finish()
check(not short.references_found, "a tone shorter than 3 s is not a reference")
fresh_events = []
fresh = sim.SimulatorCore("transparent", None, emit=fresh_events.append)
feed(fresh, np.concatenate([silence(1), tone(6, -6.7), silence(40), burst(5, -6.2)]), silence(52))
fresh.finish()
last = [e for e in fresh_events if e["event"] == "attempt_end"][-1]
check(abs(last["directions"]["A->B"]["peak_vs_reference_db"] - 0.5) < 0.05,
      "a reference measured in this run is used at once for the peak check")
reference = {"directions": refs}
try:
    sim.SimulatorCore("moderate", None)
    check(False, "a channel mode without reference is refused")
except ValueError:
    check(True, "a channel mode without reference is refused")

# ---------------------------------------------------------------- attempts, seeds and validity
events = []
core = sim.SimulatorCore("moderate", reference, first_attempt=4, emit=events.append, snr_reference="peak")
a_peak = refs["A->B"]["tone_peak_dbfs"]
b_peak = refs["B->A"]["tone_peak_dbfs"]
left = np.concatenate([silence(1), burst(8, a_peak), silence(5), burst(4, a_peak),       # attempt 4
                       silence(35), burst(6, a_peak + 2.0),                               # attempt 5: peak off
                       silence(32), burst(5, a_peak)])                                    # attempt 6: dropout
right = np.concatenate([silence(10), burst(3, b_peak), silence(len(left) / FS)])[:len(left)]
drop_block = (len(left) - seconds(2)) // B
out = feed(core, left, right, status_at={drop_block: {"output_underflow": True}})
core.finish()
starts = [e for e in events if e["event"] == "attempt_start"]
ends = [e for e in events if e["event"] == "attempt_end"]
check([e["attempt"] for e in starts] == [4, 5, 6], "attempts start after 30 s of silence and count from 4")
check(starts[0]["seeds"]["A->B"] == [cm.SEED_ROOT, 2, 0, 4], "each attempt records its seeds")
check(ends[0]["valid"] and ends[0]["directions"]["A->B"]["peak_vs_reference_db"] == 0.0,
      "attempt 4 is valid (modem peak equals the reference)")
check(not ends[1]["valid"] and any("peak +2.00 dB" in p for p in ends[1]["problems"]),
      "attempt 5 is not valid: the modem peak is 2 dB above the reference")
check(not ends[2]["valid"] and ends[2]["xruns"]["output_underflow"] == 1,
      "attempt 6 is not valid: an audio dropout was reported")
check("mean_power_vs_reference_db" in ends[0]["directions"]["A->B"],
      "the modem's mean power relative to the reference is recorded")
check(np.max(np.abs(out)) < 1.0, "no clipping in the moderate condition")
poor = sim.SimulatorCore("poor", reference, emit=lambda e: None, snr_reference="peak")
check(np.max(np.abs(feed(poor, burst(20, a_peak), burst(20, b_peak)))) < 1.0,
      "no clipping at -3 dB SNR with the modems' peak (the -12 dB output gain leaves headroom)")

# The same input gives the same output (seeds), and the channel actually fades.
again = sim.SimulatorCore("moderate", reference, first_attempt=4, emit=lambda e: None, snr_reference="peak")
check(np.array_equal(feed(again, left, right), out), "a replay gives the same output, sample for sample")

# ---------------------------------------------------------------- command line, offline
tmp = Path(tempfile.mkdtemp(prefix="hf_arq_sim_"))
cal_l = np.concatenate([silence(2), tone(6, -6.7), silence(4)])
cal_r = np.concatenate([silence(6), tone(6, -6.5)])
n = max(len(cal_l), len(cal_r))
cal = np.stack([np.pad(cal_l, (0, n - len(cal_l))), np.pad(cal_r, (0, n - len(cal_r)))], axis=1)
with wave.open(str(tmp / "cal.wav"), "wb") as w:
    w.setnchannels(2), w.setsampwidth(2), w.setframerate(FS)
    w.writeframes((cal * 32767).astype("<i2").tobytes())
run = subprocess.run([sys.executable, str(ROOT / "smoke" / "channel_sim.py"), "offline", "--mode", "transparent",
                      "--wav-in", str(tmp / "cal.wav"), "--wav-out", str(tmp / "out.wav"),
                      "--results", str(tmp / "results")], capture_output=True, text=True)
print(run.stdout, run.stderr)
saved = json.loads((tmp / "results" / "simulator" / "reference.json").read_text(encoding="utf-8"))
check(set(saved["directions"]) == {"A->B", "B->A"}, "the reference is saved for the channel modes")
run = subprocess.run([sys.executable, str(ROOT / "smoke" / "channel_sim.py"), "offline", "--mode", "poor",
                      "--snr-reference", "peak",
                      "--wav-in", str(tmp / "cal.wav"), "--wav-out", str(tmp / "out2.wav"),
                      "--results", str(tmp / "results")], capture_output=True, text=True)
logs = list((tmp / "results" / "simulator").glob("*_poor/sim_log.jsonl"))
records = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
check(run.returncode == 0 and records[0]["event"] == "start" and records[-1]["event"] == "stop",
      "a channel run reads the saved reference and writes its log")
run = subprocess.run([sys.executable, str(ROOT / "smoke" / "channel_sim.py"), "offline", "--mode", "good",
                      "--snr-reference", "peak",
                      "--wav-in", str(tmp / "cal.wav"), "--wav-out", str(tmp / "out3.wav"),
                      "--results", str(tmp / "empty")], capture_output=True, text=True)
check(run.returncode == 2 and "NO REFERENCE" in run.stdout, "without a reference the channel modes refuse to start")

# The status file is best effort: a busy target (Windows "Access is denied") never raises.
from unittest import mock
log = sim.Log(tmp / "statuslog")
with mock.patch("pathlib.Path.replace", side_effect=PermissionError(5, "Access is denied")):
    log.write_status({"x": 1})
log.write_status({"x": 2})
check(json.loads((tmp / "statuslog" / "status.json").read_text(encoding="utf-8")) == {"x": 2},
      "a busy status file is skipped, not fatal, and the next update succeeds")
log.close()

# ---------------------------------------------------------------- equal RMS SNR (primary)
arm = "VARA HF licensed"
try:
    sim.SimulatorCore("moderate", reference, arm=arm, modem_power={})
    check(False, "an RMS channel run without the arm's power reference is refused")
except ValueError:
    check(True, "an RMS channel run without the arm's power reference is refused")
# Measure the arm's power in Transparent mode: CAL tone first (excluded), then modem bursts.
events = []
tcore = sim.SimulatorCore("transparent", reference, arm=arm, emit=events.append)
t_left = np.concatenate([silence(1), tone(6, -6.7), silence(35), burst(20, -6.0), silence(35), burst(10, -6.0)])
t_right = np.concatenate([silence(50), burst(4, -6.4), silence(len(t_left) / FS)])[:len(t_left)]
feed(tcore, t_left, t_right)
status = tcore.status()
check(status["run_power"]["A->B"]["active_s"] >= 29, "the run power excludes the calibration tone")
check(abs(status["base_power"]["A->B"]["active_s"] - 20) < 1,
      "the power reference is the first modem attempt (E0-1 16 B), not the whole run")
tcore.finish()
check([e for e in events if e["event"] == "attempt_end"][0]["calibration_tone"] is True,
      "the attempt with the calibration tone is marked")
import sim_control
saved_text = sim_control.save_modem_power(tmp, arm, status)
check(arm in saved_text, "the power reference is saved for the arm")
power = sim_control.read_modem_power(tmp)
events = []
rcore = sim.SimulatorCore("moderate", reference, first_attempt=1, emit=events.append, snr_reference="rms",
                          arm=arm, modem_power=power)
r_left = np.concatenate([silence(1), burst(12, -6.0), silence(35), burst(8, -2.0)])
feed(rcore, r_left, np.concatenate([silence(5), burst(3, -6.4), silence(len(r_left) / FS)])[:len(r_left)])
rcore.finish()
ends = [e for e in events if e["event"] == "attempt_end"]
eff = ends[0]["directions"]["A->B"]["effective_snr_db"]
check(abs(eff - 3.0) < 0.5, f"the effective RMS SNR equals the condition's SNR ({eff:+.2f} dB vs +3 dB)")
check(ends[0]["valid"] and ends[0]["arm"] == arm and ends[0]["snr_reference"] == "rms",
      "a burst 0.5 dB above the tone's peak is fine in RMS mode (no ±1 dB peak rule)")
check(not ends[1]["valid"] and any("ceiling" in p for p in ends[1]["problems"]),
      "a peak above -3 dBFS is not counted (safety ceiling)")
print("ALL SIMULATOR RUNTIME TESTS PASSED")
