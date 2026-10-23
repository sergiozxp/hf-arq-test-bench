"""Bench calibration: signal, analysis and the two-PC timeline (file audio backend)."""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "smoke"))
import bench_audio as audio
import bench_calibration as cal


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


RATE = cal.RATE


def recording(ppm=40.0, gain_db=-3.0, noise_dbfs=-80.0, cubic=0.0, seconds=20.0, tone_at=6.0, right=True):
    f = cal.TONE_HZ * (1 + ppm * 1e-6)
    g = 10 ** (gain_db / 20) * cal.TONE_PEAK * 32767
    sigma = 32768 * 10 ** (noise_dbfs / 20)
    rng = random.Random(7)
    out = array("h")
    for n in range(int(seconds * RATE)):
        v = rng.gauss(0.0, sigma)
        k = n - int(tone_at * RATE)
        if 0 <= k < int(cal.TONE_S * RATE):
            x = math.sin(2 * math.pi * f * k / RATE + 0.4)
            v += g * (x + cubic * x ** 3)
        v = max(-32768, min(32767, int(round(v))))
        out.append(v)
        out.append(v if right else 0)
    return out.tobytes()


# ---------------------------------------------------------------- signal
signal = array("h")
signal.frombytes(cal.test_signal())
check(len(signal) == 2 * int((cal.PRE_S + cal.TONE_S + cal.POST_S) * RATE), "test signal has the planned length")
check(max(signal) <= 16384 and min(signal) >= -16384, "test signal peaks at -6 dBFS")
check(signal[0:2 * int(cal.PRE_S * RATE)].count(0) == 2 * int(cal.PRE_S * RATE), "it starts with digital silence")
measured = cal.analyze(cal.test_signal() + bytes(4 * RATE))
check(measured["L"]["tone_found"] and abs(measured["L"]["tone_peak_dbfs"] + 6.0) < 0.2,
      "the analysis reads the test signal itself at -6 dBFS peak")

# ---------------------------------------------------------------- analysis
clean = cal.analyze(recording())
left = clean["L"]
check(abs(left["snr_db"] - 68.0) < 1.5 and abs(left["sinad_db"] - 68.0) < 1.5, "SNR and SINAD of a clean path")
check(abs(left["clock_offset_ppm"] - 40.0) < 2, "the clock offset is measured in ppm")
check(abs(left["tone_peak_dbfs"] + 9.0) < 0.3, "the received tone level is measured")
check(cal.verdict(clean)[0] == "ok", "a clean path is ok")
distorted = cal.analyze(recording(cubic=0.01))["L"]
check(abs(distorted["sinad_db"] - 52.0) < 2 and distorted["snr_db"] > 60, "distortion lowers SINAD, not SNR")
noisy = cal.analyze(recording(noise_dbfs=-40))
check(abs(noisy["L"]["snr_db"] - 28.0) < 1.5 and cal.verdict(noisy)[0] == "review", "a noisy path needs review")
clipped = cal.analyze(recording(gain_db=9))
check(clipped["L"]["clipped_samples"] > 0 and cal.verdict(clipped)[0] == "review", "clipping is reported")
one_side = cal.analyze(recording(right=False))
state, reasons = cal.verdict(one_side)
check(state == "ok" and any(r.startswith("right channel") for r in reasons),
      "a silent right channel is a note only (the modems use the left channel)")
check(cal.analyze(bytes(4 * RATE * 10))["L"]["tone_found"] is False, "silence has no tone")
check(audio.match_device("UCA222-A (USB Audio CODEC )", ["Speakers (Realtek)", "UCA222-A (USB Audio CODEC )"]) == 1,
      "device names match")
check(audio.match_device("UCA222-B (USB Audio CODEC )", ["UCA222-B (USB Audio CODEC"]) == 0,
      "names truncated by Windows still match")
try:
    audio.match_device("UCA222-A (USB Audio CODEC )", ["UCA222-B (USB Audio CODEC )"])
    check(False, "a missing device is reported")
except audio.AudioError:
    check(True, "a missing device is reported")
if sys.platform == "win32":
    print("Windows audio devices:", audio.output_devices(), audio.input_devices())
    check(isinstance(audio.output_devices(), list), "winmm device listing works")

# ---------------------------------------------------------------- the two-PC timeline
temporary = Path(tempfile.mkdtemp(prefix="hf_arq_cal_"))
env = dict(os.environ, HF_ARQ_AUDIO_DIR=str(temporary / "cable"), HF_ARQ_AUDIO_GAIN_DB="-3",
           HF_ARQ_AUDIO_NOISE_DBFS="-75", PYTHONUTF8="1")
procs = {}
for pc in ("B", "A"):
    folder = temporary / pc
    folder.mkdir()
    (folder / "smoke_config.toml").write_text(
        f'[general]\nresults_dir = "results"\napp_version = "test"\nprotocol_version = "5.2"\n'
        f'[tnc]\nmycall = "XQ2CG-{1 if pc == "A" else 2}"\npeer_call = "XQ2CG-{2 if pc == "A" else 1}"\n',
        encoding="utf-8")
    command = [sys.executable, "-u", str(ROOT / "smoke" / "bench_calibration.py"), pc,
               "--input", f"UCA222-{pc}", "--output", f"UCA222-{pc}"]
    procs[pc] = subprocess.Popen(command, cwd=folder, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
    if pc == "B":
        time.sleep(5)   # the operator starts PC A after PC B shows "recording"
outputs = {pc: p.communicate(timeout=300)[0] for pc, p in procs.items()}
for pc, text in outputs.items():
    print(f"--- PC {pc}\n{text}")
records = {}
for pc in ("A", "B"):
    found = list((temporary / pc / "results" / "calibration").glob("*/calibration.json"))
    check(len(found) == 1 and (found[0].parent / "recording.wav").is_file(), f"PC {pc} saves its record and recording")
    records[pc] = json.loads(found[0].read_text(encoding="utf-8"))
check(records["B"]["direction"] == "A->B" and records["A"]["direction"] == "B->A", "each PC measures the path it receives")
for pc, rec in records.items():
    lft = rec["channels"]["L"]
    check(rec["verdict"] == "ok" and abs(lft["snr_db"] - 63) < 2 and abs(lft["tone_peak_dbfs"] + 9) < 0.5,
          f"PC {pc}: the simulated cable is measured (SNR {lft['snr_db']} dB)")
    check(rec["max_simulated_snr_db"] is not None and rec["max_simulated_snr_db"] < lft["snr_db"],
          f"PC {pc}: the usable simulated SNR limit is recorded")
check(procs["A"].returncode == 0 and procs["B"].returncode == 0, "both PCs finish with exit code 0")
print("ALL CALIBRATION TESTS PASSED")
