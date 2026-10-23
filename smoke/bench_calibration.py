"""Bench calibration (protocol v5.2): the bench's own measurement of each audio path.

With both modems stopped, each PC plays a known test signal into the cable and the
other PC records it. The signal is 4 s of digital silence, 6 s of a 1500 Hz tone at
-6 dBFS peak (the TX peak both modems use on this bench) and 4 s of silence. From
the recording the receiving PC measures, for the left channel (the one both modems
use) and the right channel:

  tone level (dBFS), noise floor (dBFS), SNR = tone / noise in the silent parts,
  SINAD = tone / everything else during the tone (noise plus distortion),
  clipped samples, DC offset, and the sound-card clock offset in ppm.

USAGE (the two PCs follow a fixed timeline; no network is needed)
  PC B, first:  python bench_calibration.py B --input <device> --output <device>
                records 45 s (path A->B), then plays the signal (path B->A)
  PC A, 2-30 s after PC B shows "recording":
                python bench_calibration.py A --input <device> --output <device>
                plays the signal (path A->B), then records 60 s (path B->A)

Each PC saves results/calibration/<time>_<path>/calibration.json and recording.wav.
The simulated channel may only be used at SNRs at least MARGIN_DB below the
bench SINAD of the path that carries the data.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from array import array
from datetime import datetime, timezone
from operator import mul
from pathlib import Path

import bench_audio as audio
from smoke_common import load_config

RATE = audio.RATE
TONE_HZ = 1500.0
TONE_PEAK = 0.5            # -6 dBFS peak, the TX peak of both modems on this bench
PRE_S, TONE_S, POST_S = 4.0, 6.0, 4.0
FADE_S = 0.02
BLOCK = 2400               # 50 ms; a whole number of tone periods (1500 Hz -> 75)
RECORD_FIRST_S = 45.0      # PC B
RECORD_SECOND_S = 60.0     # PC A
START_WINDOW_S = (2, 30)   # PC A starts this long after PC B shows "recording"

MIN_SNR_DB = 40.0
MIN_SINAD_DB = 35.0
MAX_PPM = 100.0
MAX_LR_DIFF_DB = 3.0
MARGIN_DB = 10.0


# ------------------------------------------------------------------ signal
def test_signal() -> bytes:
    """Stereo 16-bit PCM: silence, tone (same on both channels), silence."""
    n_pre, n_tone, n_post = (int(s * RATE) for s in (PRE_S, TONE_S, POST_S))
    fade = int(FADE_S * RATE)
    amp = TONE_PEAK * 32767
    tone = []
    for n in range(n_tone):
        g = min(1.0, n / fade, (n_tone - 1 - n) / fade)
        tone.append(int(round(amp * g * math.sin(2 * math.pi * TONE_HZ * n / RATE))))
    mono = [0] * n_pre + tone + [0] * n_post
    stereo = array("h", (v for v in mono for _ in range(2)))
    return stereo.tobytes()


# ------------------------------------------------------------------ analysis
def _solve3(m: list[list[float]], v: list[float]) -> list[float]:
    """Solve a 3x3 linear system by Gaussian elimination with pivoting."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        for r in range(col + 1, 3):
            f = a[r][col] / a[col][col]
            for c in range(col, 4):
                a[r][c] -= f * a[col][c]
    x = [0.0, 0.0, 0.0]
    for r in (2, 1, 0):
        x[r] = (a[r][3] - sum(a[r][c] * x[c] for c in range(r + 1, 3))) / a[r][r]
    return x


def _tone_fit(samples: array, starts: list[int], freq: float) -> tuple[float, float]:
    """Least-squares fit of a*cos + b*sin + c at freq in every block.

    Returns the mean tone power (a^2+b^2)/2 and the mean residual power (noise plus
    distortion). A full fit, unlike a single DFT bin, has no leakage from the
    tone's negative-frequency image, so the residual is not biased by the tone.
    """
    cos_t = [math.cos(2 * math.pi * freq * n / RATE) for n in range(BLOCK)]
    sin_t = [math.sin(2 * math.pi * freq * n / RATE) for n in range(BLOCK)]
    g = [[sum(map(mul, cos_t, cos_t)), sum(map(mul, cos_t, sin_t)), sum(cos_t)],
         [0.0, sum(map(mul, sin_t, sin_t)), sum(sin_t)],
         [0.0, 0.0, float(BLOCK)]]
    g[1][0], g[2][0], g[2][1] = g[0][1], g[0][2], g[1][2]
    tone = resid = 0.0
    for start in starts:
        blk = samples[start:start + BLOCK]
        p = [float(sum(map(mul, blk, cos_t))), float(sum(map(mul, blk, sin_t))), float(sum(blk))]
        a, b, c = _solve3(g, p)
        energy = float(sum(map(mul, blk, blk)))
        tone += (a * a + b * b) / 2
        resid += max(energy - (a * p[0] + b * p[1] + c * p[2]), 0.0) / BLOCK
    return tone / len(starts), resid / len(starts)


def _blocks(samples: array) -> list[dict]:
    cos_t = [math.cos(2 * math.pi * TONE_HZ * n / RATE) for n in range(BLOCK)]
    sin_t = [math.sin(2 * math.pi * TONE_HZ * n / RATE) for n in range(BLOCK)]
    out = []
    for start in range(0, len(samples) - BLOCK + 1, BLOCK):
        blk = samples[start:start + BLOCK]
        s = sum(blk)
        mean = s / BLOCK
        power = sum(map(mul, blk, blk)) / BLOCK - mean * mean
        re = sum(map(mul, blk, cos_t))
        im = -sum(map(mul, blk, sin_t))
        tone_power = 2 * (re * re + im * im) / (BLOCK * BLOCK)
        # absolute phase at the block start relative to the nominal tone
        phase = math.atan2(im, re) - 2 * math.pi * ((TONE_HZ * start / RATE) % 1.0)
        out.append({"start": start, "power": power, "tone": tone_power, "dc": mean,
                    "phase": phase, "peak": max(max(blk), -min(blk))})
    return out


def _db(ratio: float) -> float | None:
    return None if ratio <= 0 else round(10 * math.log10(ratio), 1)


def _power_dbfs(power: float) -> float | None:
    # RMS relative to full scale (32768): a full-scale sine reads -3 dBFS.
    return None if power <= 0 else round(10 * math.log10(power / (32768.0 ** 2)), 1)


def analyze_channel(samples: array) -> dict:
    blocks = _blocks(samples)
    if not blocks:
        return {"tone_found": False, "reason": "recording too short"}
    best = max(b["tone"] for b in blocks)
    floor = sorted(b["power"] for b in blocks)[len(blocks) // 10]
    on = [b["tone"] > best / 4 and b["tone"] > 100 * max(floor, 1e-9) for b in blocks]
    # longest run of tone blocks
    run, best_run, cur_start = 0, (0, -1), 0
    for i, flag in enumerate(on):
        if flag:
            if run == 0:
                cur_start = i
            run += 1
            if run > best_run[1] - best_run[0] + 1:
                best_run = (cur_start, i)
        else:
            run = 0
    first, last = best_run
    n_tone = last - first + 1
    if last < 0 or n_tone * BLOCK < 3 * RATE:
        return {"tone_found": False, "reason": "no 1500 Hz tone of at least 3 s in the recording",
                "noise_rms_dbfs": _power_dbfs(floor)}
    guard = int(0.5 * RATE / BLOCK)
    core = blocks[first + guard:last - guard + 1]
    span = int(3.0 * RATE / BLOCK)
    noise = blocks[max(0, first - guard - span):max(0, first - guard)] + \
        blocks[last + guard + 1:last + guard + 1 + span]
    # Clock offset: phase drift of the tone across the core, unwrapped block by block.
    phases = [b["phase"] for b in core]
    unwrapped = [phases[0]]
    for p in phases[1:]:
        d = (p - unwrapped[-1] + math.pi) % (2 * math.pi) - math.pi
        unwrapped.append(unwrapped[-1] + d)
    t = [b["start"] / RATE for b in core]
    mt, mp = sum(t) / len(t), sum(unwrapped) / len(unwrapped)
    slope = sum((a - mt) * (b - mp) for a, b in zip(t, unwrapped)) / sum((a - mt) ** 2 for a in t)
    freq = TONE_HZ + slope / (2 * math.pi)
    # Tone and residual from a fit at the measured frequency, so that a clock offset
    # does not leak tone power into the SINAD residual.
    tone_power, residual = _tone_fit(samples, [b["start"] for b in core], freq)
    residual = max(residual, 1e-9)
    result = {"tone_found": True, "tone_s": round(n_tone * BLOCK / RATE, 2),
              "tone_rms_dbfs": _power_dbfs(tone_power),
              "tone_peak_dbfs": _power_dbfs(2 * tone_power),   # peak of a sine = RMS x sqrt(2)
              "sinad_db": _db(tone_power / residual),
              "dc_offset": round(sum(b["dc"] for b in core) / len(core), 1),
              "clipped_samples": sum(1 for v in samples if v >= 32767 or v <= -32768),
              "clock_offset_ppm": round((freq / TONE_HZ - 1) * 1e6, 1)}
    if len(noise) * BLOCK >= RATE:
        noise_power = max(sum(b["power"] for b in noise) / len(noise), 1e-9)
        result["noise_rms_dbfs"] = _power_dbfs(noise_power)
        result["snr_db"] = _db(tone_power / noise_power)
    else:
        result.update({"noise_rms_dbfs": None, "snr_db": None})
    return result


def analyze(pcm: bytes) -> dict:
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) - len(pcm) % audio.FRAME_BYTES])
    if sys.byteorder != "little":
        samples.byteswap()
    return {"L": analyze_channel(samples[0::2]), "R": analyze_channel(samples[1::2])}


def verdict(channels: dict) -> tuple[str, list[str]]:
    left, right = channels["L"], channels["R"]
    reasons = []
    if not left.get("tone_found"):
        return "review", ["left channel: " + left.get("reason", "no tone")]
    if left.get("snr_db") is None:
        reasons.append("not enough silence around the tone to measure the noise floor")
    elif left["snr_db"] < MIN_SNR_DB:
        reasons.append(f"left SNR {left['snr_db']} dB is below {MIN_SNR_DB} dB")
    if left["sinad_db"] is not None and left["sinad_db"] < MIN_SINAD_DB:
        reasons.append(f"left SINAD {left['sinad_db']} dB is below {MIN_SINAD_DB} dB")
    if left["clipped_samples"]:
        reasons.append(f"{left['clipped_samples']} clipped samples: lower the recording level")
    if abs(left["clock_offset_ppm"]) > MAX_PPM:
        reasons.append(f"clock offset {left['clock_offset_ppm']} ppm exceeds {MAX_PPM} ppm")
    if right.get("tone_found") and left["tone_rms_dbfs"] is not None and right["tone_rms_dbfs"] is not None \
            and abs(left["tone_rms_dbfs"] - right["tone_rms_dbfs"]) > MAX_LR_DIFF_DB:
        reasons.append(f"left and right differ by more than {MAX_LR_DIFF_DB} dB: check the cable")
    if not right.get("tone_found"):
        reasons.append("right channel: no tone (only the left channel is used by the modems)")
    # Notes that do not limit the usable SNR of the path the modems use.
    notes = ("right channel", "clock offset", "left and right differ")
    serious = [r for r in reasons if not r.startswith(notes)]
    return ("ok" if not serious else "review"), reasons


# ------------------------------------------------------------------ run
def measure(role: str, input_device: str, output_device: str, cfg: dict, log=print) -> dict:
    """Run this PC's part of the calibration and return the saved record."""
    general = cfg.get("general", {})
    tnc = cfg.get("tnc", cfg.get("mercury", {}))
    signal = test_signal()
    started = datetime.now(timezone.utc)
    if role == "B":
        direction = "A->B"
        log(f"recording {RECORD_FIRST_S:.0f} s from '{input_device}' (path A->B). "
            f"Start PC A now, within {START_WINDOW_S[1]} s.")
        pcm = audio.record(input_device, RECORD_FIRST_S)
        log(f"playing the test signal on '{output_device}' (path B->A)…")
        audio.play(output_device, signal)
    else:
        direction = "B->A"
        log(f"playing the test signal on '{output_device}' (path A->B)…")
        audio.play(output_device, signal)
        log(f"recording {RECORD_SECOND_S:.0f} s from '{input_device}' (path B->A)…")
        pcm = audio.record(input_device, RECORD_SECOND_S)
    log("analysing the recording…")
    channels = analyze(pcm)
    state, reasons = verdict(channels)
    left = channels["L"]
    limit = None
    if left.get("tone_found") and left.get("sinad_db") is not None:
        floor_db = min(v for v in (left.get("snr_db"), left["sinad_db"]) if v is not None)
        limit = round(floor_db - MARGIN_DB, 1)
    record = {
        "kind": "bench_calibration", "direction": direction, "role": role,
        "mycall": tnc.get("mycall"), "peer_call": tnc.get("peer_call"),
        "app_version": general.get("app_version"), "protocol_version": general.get("protocol_version"),
        "started_utc": started.isoformat(timespec="seconds"),
        "input_device": input_device, "output_device": output_device,
        "signal": {"tone_hz": TONE_HZ, "tone_peak_dbfs": round(20 * math.log10(TONE_PEAK), 1),
                   "pre_s": PRE_S, "tone_s": TONE_S, "post_s": POST_S, "rate": RATE,
                   "format": "16 bit stereo, same signal on both channels"},
        "channels": channels, "verdict": state, "reasons": reasons,
        "max_simulated_snr_db": limit,
        "thresholds": {"min_snr_db": MIN_SNR_DB, "min_sinad_db": MIN_SINAD_DB,
                       "max_clock_offset_ppm": MAX_PPM, "margin_db": MARGIN_DB},
    }
    out = Path(general.get("results_dir", "results")) / "calibration" / \
        f"{started:%Y%m%dT%H%M%SZ}_{direction.replace('->', 'to')}"
    out.mkdir(parents=True, exist_ok=True)
    audio.write_wav(out / "recording.wav", pcm)
    path = out / "calibration.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["path"] = str(path)
    return record


def describe(record: dict) -> str:
    left = record["channels"]["L"]
    if not left.get("tone_found"):
        return f"{record['direction']}: NO TONE RECEIVED ({left.get('reason')})."
    return (f"{record['direction']}: tone {left['tone_peak_dbfs']} dBFS peak, noise "
            f"{left.get('noise_rms_dbfs')} dBFS, SNR {left.get('snr_db')} dB, SINAD {left['sinad_db']} dB, "
            f"clock {left['clock_offset_ppm']:+} ppm, clipped {left['clipped_samples']}.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bench calibration: measure one audio path per PC.")
    ap.add_argument("role", choices=["A", "B"])
    ap.add_argument("--input", required=True, help="recording device (the signal from the other PC)")
    ap.add_argument("--output", required=True, help="playback device (the signal to the other PC)")
    ap.add_argument("--config", default="smoke_config.toml")
    args = ap.parse_args()
    cfg = load_config(args.config)
    try:
        record = measure(args.role, args.input, args.output, cfg, log=lambda t: print(t, flush=True))
    except audio.AudioError as error:
        print(f"CALIBRATION FAILED: {error}", flush=True)
        return 1
    print(describe(record), flush=True)
    for reason in record["reasons"]:
        print("Note: " + reason, flush=True)
    print(f"Calibration saved to: {record['path']}", flush=True)
    return 0 if record["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
