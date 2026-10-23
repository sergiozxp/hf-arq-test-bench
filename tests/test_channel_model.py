"""Offline validation of the Phase 1 channel simulator (no audio hardware).

Checks the programmed Doppler spread (2 sigma), the differential delay, equal path
powers, unit mean channel gain, the SNR in 3 kHz, determinism of the seeds, and the
transparent mode. These are the "simulator software" validation steps of
docs/phase1_channel_design.md.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import channel_model as cm


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def db(x: float) -> float:
    return 10 * math.log10(x)


# ---------------------------------------------------------------- Doppler spread and path power
for key, cond in cm.CONDITIONS.items():
    tap = cm.FadingTap(cond.spread_hz, np.random.default_rng(7))
    n_low = 1 << 20
    tap._ensure(n_low)
    g = tap.low[:n_low]
    seg = 1 << 14
    spectrum = np.zeros(seg)
    for i in range(0, n_low - seg + 1, seg // 2):
        part = g[i:i + seg] * np.hanning(seg)
        spectrum += np.abs(np.fft.fft(part)) ** 2
    f = np.fft.fftfreq(seg, 1 / cm.FADING_RATE)
    sigma = math.sqrt(np.sum(f ** 2 * spectrum) / np.sum(spectrum))
    expected = cond.spread_hz / 2
    check(abs(sigma / expected - 1) < 0.08,
          f"{key}: Doppler 2 sigma = {2 * sigma:.3f} Hz (programmed {cond.spread_hz} Hz)")
    power = float(np.mean(np.abs(g) ** 2))
    check(abs(power - 0.5) < 0.05, f"{key}: path mean power {power:.3f} (programmed 0.5)")

# ---------------------------------------------------------------- differential delay
rng = np.random.default_rng(1)
x = rng.standard_normal(cm.FS * 2) * 0.1
for key, cond in cm.CONDITIONS.items():
    outs = []
    for gains in ((1, 0), (0, 1)):
        path = cm.ChannelPath(key, "A->B", cm.reference_power())
        outs.append(np.concatenate([path.process(x[i:i + cm.BLOCK], static_gains=gains, noise=False)
                                    for i in range(0, len(x), cm.BLOCK)]))
    first, second = outs
    lags = np.arange(0, 200)
    corr = [float(np.dot(first[:len(first) - lag], second[lag:])) for lag in lags]
    lag = int(lags[int(np.argmax(corr))])
    expected = int(round(cond.delay_ms * 1e-3 * cm.FS))
    check(lag == expected, f"{key}: second path delayed by {lag} samples = {lag / cm.FS * 1e3:.2f} ms "
                           f"(programmed {cond.delay_ms} ms)")
    check(np.allclose(first[:len(first) - lag], second[lag:], atol=1e-9),
          f"{key}: the two paths carry the same signal")

# ---------------------------------------------------------------- band-pass keeps the tone
tone = 0.5 * np.sin(2 * np.pi * 1500 * np.arange(cm.FS * 2) / cm.FS)
path = cm.ChannelPath("good", "A->B", cm.reference_power())
y = np.concatenate([path.process(tone[i:i + cm.BLOCK], static_gains=(1, 0), noise=False)
                    for i in range(0, len(tone), cm.BLOCK)])
gain_db = db(np.mean(y[cm.FS:] ** 2) / np.mean(tone[cm.FS:] ** 2))
check(abs(gain_db - cm.OUTPUT_GAIN_DB) < 0.1, f"a 1500 Hz tone passes with the output gain ({gain_db:.2f} dB)")

# ---------------------------------------------------------------- unit mean channel gain with fading
signal = rng.standard_normal(cm.FS * 300)
band = cm.StreamFIR(cm.noise_bandpass())
signal = np.concatenate([band.process(signal[i:i + cm.BLOCK]) for i in range(0, len(signal), cm.BLOCK)])
p_in = float(np.mean(signal[cm.FS:] ** 2))
path = cm.ChannelPath("poor", "A->B", cm.reference_power(), attempt=3)
y = np.concatenate([path.process(signal[i:i + cm.BLOCK], noise=False) for i in range(0, len(signal), cm.BLOCK)])
ratio = float(np.mean(y[cm.FS:] ** 2)) / (p_in * 10 ** (cm.OUTPUT_GAIN_DB / 10))
check(abs(db(ratio)) < 0.6, f"poor channel over 300 s: mean power gain {db(ratio):+.2f} dB (expected 0 dB)")

# ---------------------------------------------------------------- SNR in 3 kHz
for key, cond in cm.CONDITIONS.items():
    path = cm.ChannelPath(key, "A->B", cm.reference_power())
    silence = np.zeros(cm.FS * 60)
    w = np.concatenate([path.process(silence[i:i + cm.BLOCK]) for i in range(0, len(silence), cm.BLOCK)])
    w = w[cm.FS:] / 10 ** (cm.OUTPUT_GAIN_DB / 20)
    snr = db(cm.reference_power() / float(np.mean(w ** 2)))
    check(abs(snr - cond.snr_db) < 0.2, f"{key}: SNR {snr:+.2f} dB in 3 kHz (programmed {cond.snr_db:+.0f} dB)")
    spec = np.abs(np.fft.rfft(w)) ** 2
    freqs = np.fft.rfftfreq(len(w), 1 / cm.FS)
    inside = spec[(freqs >= 300) & (freqs <= 3300)].sum() / spec.sum()
    check(inside > 0.97, f"{key}: {inside * 100:.1f} % of the noise lies in 300-3300 Hz")

# ---------------------------------------------------------------- seeds
x = rng.standard_normal(cm.FS * 5) * 0.2


def run(block: int, attempt: int, direction: str = "A->B", condition: str = "moderate") -> np.ndarray:
    path = cm.ChannelPath(condition, direction, cm.reference_power(), attempt=attempt)
    return np.concatenate([path.process(x[i:i + block]) for i in range(0, len(x), block)])


a, b = run(1024, 5), run(480, 5)
check(np.allclose(a, b, atol=1e-12), "the same seed gives the same output, whatever the block size")
check(not np.allclose(a, run(1024, 6)), "another attempt number gives another trace")
check(not np.allclose(a, run(1024, 5, direction="B->A")), "the two directions have independent traces")
seq = cm.seeds("poor", "B->A", 30)
check(len(seq) == 3 and seq[0].entropy == seq[1].entropy, "three seed streams per attempt (paths 1, 2, noise)")

# ---------------------------------------------------------------- transparent
path = cm.ChannelPath("transparent", "A->B", cm.reference_power())
check(np.array_equal(path.process(x[:1024]), x[:1024] * 10 ** (cm.OUTPUT_GAIN_DB / 20)),
      "transparent mode only applies the output gain")

# ---------------------------------------------------------------- real-time margin
import time
paths = [cm.ChannelPath("poor", d, cm.reference_power()) for d in ("A->B", "B->A")]
block = rng.standard_normal(cm.BLOCK) * 0.1
start = time.perf_counter()
for _ in range(470):          # 10 s of audio, both directions
    for p in paths:
        p.process(block)
load = (time.perf_counter() - start) / 10.0
print(f"processing load for both directions: {load * 100:.1f} % of real time")
check(load < 0.5, "the simulator runs well within real time")
print("ALL CHANNEL MODEL TESTS PASSED")
