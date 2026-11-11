"""Channel simulator for PC B (protocol v5.3, docs/phase1_channel_design.md).

UCA-S input L  (from PC A)  -> channel A->B -> UCA-S output L (to PC B's modem)
UCA-S input R  (from PC B)  -> channel B->A -> UCA-S output R (to PC A's modem)

Modes: transparent (output gain only) or good / moderate / poor (Watterson + noise).

In transparent mode the simulator also measures the reference: when the bench
calibration tone (1500 Hz) arrives steadily for at least 3 s on an input, its power
is stored as P_ref for that direction (results/simulator/reference.json). The
channel modes use that reference and never adjust it during a run.

Attempts: the first A->B audio after at least SILENCE_S of silence starts attempt k;
both directions restart from attempt k's seeds. Each attempt is logged with its
input peak and mean power per direction, output clipping and audio dropouts.

USAGE
  python channel_sim.py list
  python channel_sim.py run --input <device> --output <device> --mode transparent|good|moderate|poor
                            [--first-attempt 1] [--results results]
  python channel_sim.py offline --wav-in in.wav --wav-out out.wav --mode ... (tests)
The run stops when the file STOP appears in its log folder (written by the GUI).
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import channel_model as cm

ACTIVE_DBFS = -50.0          # an input block above this RMS level is "audio"
SILENCE_S = 30.0             # silence that separates two attempts
TONE_MIN_S = 3.0             # steady calibration tone needed for a reference
TONE_PURITY = 0.98           # share of block power in the 1500 Hz bin
TONE_MIN_DBFS = -40.0
PEAK_TOLERANCE_DB = 1.0
STATUS_EVERY_S = 0.5
CHANNELS = ("A->B", "B->A")  # input/output L, R

_n = np.arange(cm.BLOCK)
_COS = np.cos(2 * np.pi * 1500.0 * _n / cm.FS)   # 1024 samples = 32 periods exactly
_SIN = np.sin(2 * np.pi * 1500.0 * _n / cm.FS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def dbfs_power(p: float) -> float | None:
    return None if p <= 0 else round(10 * math.log10(p), 2)


def dbfs_peak(a: float) -> float | None:
    return None if a <= 0 else round(20 * math.log10(a), 2)


def reference_path(results: Path) -> Path:
    return results / "simulator" / "reference.json"


def load_reference(results: Path) -> dict | None:
    try:
        ref = json.loads(reference_path(results).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ref if all(d in ref.get("directions", {}) for d in CHANNELS) else None


class DirectionStats:
    def __init__(self):
        self.peak = 0.0
        self.active_power = 0.0
        self.active_blocks = 0
        self.out_clipped = 0

    def summary(self, ref: dict | None) -> dict:
        mean = self.active_power / self.active_blocks if self.active_blocks else 0.0
        out = {"input_peak_dbfs": dbfs_peak(self.peak),
               "input_active_mean_dbfs": dbfs_power(mean),
               "active_s": round(self.active_blocks * cm.BLOCK / cm.FS, 2),
               "output_clipped_samples": self.out_clipped}
        if ref and self.peak > 0:
            out["peak_vs_reference_db"] = round(dbfs_peak(self.peak) - ref["tone_peak_dbfs"], 2)
            if mean > 0:
                # the same SNR referred to this modem's mean power instead of the peak sine
                out["mean_power_vs_reference_db"] = round(10 * math.log10(mean / ref["p_ref"]), 2)
        return out


class SimulatorCore:
    """Hardware-independent processing; process() is called once per audio block."""

    def __init__(self, mode: str, reference: dict | None, first_attempt: int = 1, emit=print):
        if mode != "transparent" and (mode not in cm.CONDITIONS or reference is None):
            raise ValueError("a channel mode needs a known condition and a reference from the calibration")
        self.mode, self.reference, self.emit = mode, reference, emit
        self.attempt = first_attempt - 1
        self.started = False
        self.samples = 0
        self.last_active = -1e9
        self.paths = {d: cm.ChannelPath(mode, d, (reference or {}).get("directions", {}).get(d, {})
                                        .get("p_ref", cm.reference_power()), first_attempt)
                      for d in CHANNELS}
        self.stats = {d: DirectionStats() for d in CHANNELS}
        self.xruns = {"input_overflow": 0, "output_underflow": 0}
        self.attempt_xruns = dict(self.xruns)
        self.attempt_start = None
        self.tone = {d: [] for d in CHANNELS}
        self.references_found: dict[str, dict] = {}
        self.reference_count = 0

    # -- attempts
    def _start_attempt(self, t_s: float) -> None:
        if self.started:
            self._end_attempt()
        self.attempt += 1
        self.started = True
        for d in CHANNELS:
            self.paths[d].reset(self.attempt)
            self.stats[d] = DirectionStats()
        self.attempt_xruns = {k: 0 for k in self.xruns}
        self.attempt_start = utc_now()
        self.emit({"event": "attempt_start", "attempt": self.attempt, "t_utc": self.attempt_start,
                   "stream_s": round(t_s, 3), "mode": self.mode,
                   "seeds": None if self.mode == "transparent" else
                   {d: [cm.SEED_ROOT, cm.CONDITIONS[self.mode].code, cm.DIRECTIONS[d], self.attempt]
                    for d in CHANNELS}})

    def _end_attempt(self) -> None:
        refs = (self.reference or {}).get("directions", {})
        summary = {d: self.stats[d].summary(refs.get(d)) for d in CHANNELS}
        problems = []
        if any(self.attempt_xruns.values()):
            problems.append(f"audio dropouts {self.attempt_xruns}")
        for d in CHANNELS:
            if summary[d]["output_clipped_samples"]:
                problems.append(f"{d}: {summary[d]['output_clipped_samples']} clipped output samples")
            dev = summary[d].get("peak_vs_reference_db")
            if dev is not None and abs(dev) > PEAK_TOLERANCE_DB and summary[d]["active_s"] > 1:
                problems.append(f"{d}: modem peak {dev:+.2f} dB from the reference")
        self.emit({"event": "attempt_end", "attempt": self.attempt, "t_utc": utc_now(),
                   "started_utc": self.attempt_start, "mode": self.mode, "directions": summary,
                   "xruns": dict(self.attempt_xruns), "valid": not problems, "problems": problems})

    # -- reference tone (transparent mode)
    def _tone_block(self, d: str, x: np.ndarray) -> None:
        power = float(np.mean(x * x))
        if power <= 0:
            self._close_tone(d)
            return
        re, im = float(np.dot(x, _COS)), float(np.dot(x, _SIN))
        tone = 2 * (re * re + im * im) / (cm.BLOCK * cm.BLOCK)
        if tone / power >= TONE_PURITY and 10 * math.log10(tone) >= TONE_MIN_DBFS:
            self.tone[d].append((tone, float(np.max(np.abs(x)))))
        else:
            self._close_tone(d)

    def _close_tone(self, d: str) -> None:
        run = self.tone[d]
        self.tone[d] = []
        if len(run) * cm.BLOCK / cm.FS < TONE_MIN_S:
            return
        edge = int(0.5 * cm.FS / cm.BLOCK)
        core = run[edge:-edge] or run
        p_ref = float(np.mean([t for t, _ in core]))
        found = {"p_ref": p_ref, "tone_power_dbfs": dbfs_power(p_ref),
                 "tone_peak_dbfs": dbfs_peak(math.sqrt(2 * p_ref)),
                 "measured_peak_dbfs": dbfs_peak(max(p for _, p in core)),
                 "tone_s": round(len(run) * cm.BLOCK / cm.FS, 2), "t_utc": utc_now()}
        self.references_found[d] = found
        self.reference_count += 1
        self.emit({"event": "reference", "direction": d, **found})

    # -- one block
    def process(self, block: np.ndarray, status: dict | None = None) -> np.ndarray:
        status = status or {}
        for key in self.xruns:
            if status.get(key):
                self.xruns[key] += 1
                self.attempt_xruns[key] += 1
                self.emit({"event": "xrun", "kind": key, "attempt": self.attempt, "t_utc": utc_now()})
        t_s = self.samples / cm.FS
        x = np.asarray(block, dtype=np.float64)
        rms_l = float(np.sqrt(np.mean(x[:, 0] ** 2)))
        if rms_l > 0 and 20 * math.log10(rms_l) > ACTIVE_DBFS:
            if t_s - self.last_active >= SILENCE_S or not self.started:
                self._start_attempt(t_s)
            self.last_active = t_s
        out = np.empty_like(x)
        for i, d in enumerate(CHANNELS):
            xi = x[:, i]
            st = self.stats[d]
            p = float(np.mean(xi * xi))
            if p > 0 and 10 * math.log10(p) > ACTIVE_DBFS:
                st.active_power += p
                st.active_blocks += 1
            st.peak = max(st.peak, float(np.max(np.abs(xi))))
            if self.mode == "transparent":
                self._tone_block(d, xi)
            y = self.paths[d].process(xi)
            clipped = int(np.count_nonzero(np.abs(y) >= 1.0))
            st.out_clipped += clipped
            out[:, i] = np.clip(y, -1.0, 1.0)
        self.samples += len(x)
        return out

    def finish(self) -> None:
        for d in CHANNELS:
            self._close_tone(d)
        if self.started:
            self._end_attempt()

    def status(self) -> dict:
        refs = (self.reference or {}).get("directions", {})
        return {"t_utc": utc_now(), "mode": self.mode, "attempt": self.attempt if self.started else None,
                "seconds_since_audio": round(self.samples / cm.FS - self.last_active, 1)
                if self.started else None,
                "xruns_total": dict(self.xruns),
                "directions": {d: self.stats[d].summary(refs.get(d)) for d in CHANNELS},
                "references_found": self.references_found}


# ------------------------------------------------------------------ log writer
class Log:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.file = open(folder / "sim_log.jsonl", "a", encoding="utf-8")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def emit(self, record: dict) -> None:
        self.q.put(record)

    def _run(self) -> None:
        while True:
            record = self.q.get()
            if record is None:
                break
            self.file.write(json.dumps(record) + "\n")
            self.file.flush()

    def write_status(self, status: dict) -> None:
        """Best effort: the status file is only for the GUI, so it never stops the simulator.

        On Windows the replace fails with "Access is denied" while the GUI is reading the
        file (2026-11-11, first run on the real bench); then it is retried and, if still
        busy, skipped until the next update half a second later.
        """
        try:
            tmp = self.folder / "status.tmp"
            tmp.write_text(json.dumps(status), encoding="utf-8")
            for _ in range(5):
                try:
                    tmp.replace(self.folder / "status.json")
                    return
                except PermissionError:
                    time.sleep(0.02)
        except OSError:
            pass

    def close(self) -> None:
        self.q.put(None)
        self.thread.join(timeout=5)
        self.file.close()


def save_references(results: Path, found: dict, devices: dict) -> None:
    if not found:
        return
    path = reference_path(results)
    current = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    directions = current.get("directions", {})
    directions.update(found)
    record = {"directions": directions, "devices": devices, "updated_utc": utc_now(),
              "note": "P_ref = power of the calibration tone (1500 Hz, -6 dBFS peak at the modem side) "
                      "as it arrives at the simulator input"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ audio
def audio_devices() -> dict:
    import sounddevice as sd
    import bench_audio
    hostapis = sd.query_hostapis()
    mme = next((i for i, h in enumerate(hostapis) if h["name"] == "MME"), None)
    devices = [dict(d, index=i) for i, d in enumerate(sd.query_devices())
               if mme is None or d["hostapi"] == mme]
    return {"inputs": [d["name"] for d in devices if d["max_input_channels"] >= 2],
            "outputs": [d["name"] for d in devices if d["max_output_channels"] >= 2],
            "_raw": devices, "_match": bench_audio.match_device}


def run_audio(args, folder: Path, core: SimulatorCore, log: Log) -> int:
    import sounddevice as sd
    info = audio_devices()
    raw, match = info["_raw"], info["_match"]
    ins = [d for d in raw if d["max_input_channels"] >= 2]
    outs = [d for d in raw if d["max_output_channels"] >= 2]
    dev_in = ins[match(args.input, [d["name"] for d in ins])]["index"]
    dev_out = outs[match(args.output, [d["name"] for d in outs])]["index"]
    failure: list[str] = []

    def callback(indata, outdata, frames, _time, status):
        try:
            flags = {"input_overflow": bool(status.input_overflow),
                     "output_underflow": bool(status.output_underflow)}
            outdata[:] = core.process(indata, flags).astype(np.float32)
        except Exception as error:  # noqa: BLE001 - recorded, then the stream stops
            failure.append(repr(error))
            raise sd.CallbackAbort

    stream = sd.Stream(device=(dev_in, dev_out), samplerate=cm.FS, blocksize=cm.BLOCK, channels=2,
                       dtype="float32", latency="high", callback=callback)
    with stream:
        log.emit({"event": "audio_started", "t_utc": utc_now(), "latency_s": list(stream.latency),
                  "input": args.input, "output": args.output})
        print(f"Simulator running ({args.mode}); latency {stream.latency}", flush=True)
        saved = 0
        while stream.active and not (folder / "STOP").exists():
            log.write_status(core.status())
            if args.mode == "transparent" and core.reference_count != saved:
                try:
                    save_references(Path(args.results), dict(core.references_found),
                                    {"input": args.input, "output": args.output})
                    saved = core.reference_count
                except OSError as error:   # retried at the next update and at the stop
                    log.emit({"event": "warning", "detail": f"reference not saved yet: {error!r}",
                              "t_utc": utc_now()})
            time.sleep(STATUS_EVERY_S)
    if failure:
        log.emit({"event": "error", "detail": failure[0], "t_utc": utc_now()})
        print("SIMULATOR ERROR: " + failure[0], flush=True)
        return 1
    return 0


# ------------------------------------------------------------------ offline (tests)
def run_offline(args, core: SimulatorCore) -> int:
    import wave
    with wave.open(args.wav_in, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2) / 32768.0
    out = []
    for i in range(0, len(data) - cm.BLOCK + 1, cm.BLOCK):
        out.append(core.process(data[i:i + cm.BLOCK]))
    y = np.clip(np.concatenate(out) * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(args.wav_out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(cm.FS)
        w.writeframes(y.tobytes())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 channel simulator (PC B, UCA-S).")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list stereo audio devices (MME)")
    for name in ("run", "offline"):
        p = sub.add_parser(name)
        p.add_argument("--mode", required=True, choices=["transparent", *cm.CONDITIONS])
        p.add_argument("--first-attempt", type=int, default=1)
        p.add_argument("--results", default="results")
        if name == "run":
            p.add_argument("--input", required=True)
            p.add_argument("--output", required=True)
        else:
            p.add_argument("--wav-in", required=True)
            p.add_argument("--wav-out", required=True)
    args = ap.parse_args()
    if args.command == "list":
        info = audio_devices()
        print(json.dumps({"inputs": info["inputs"], "outputs": info["outputs"]}))
        return 0
    results = Path(args.results)
    reference = load_reference(results)
    if args.mode != "transparent" and reference is None:
        print("NO REFERENCE: run the bench calibration (CAL) with the simulator in transparent mode first.",
              flush=True)
        return 2
    folder = results / "simulator" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{args.mode}"
    log = Log(folder)
    core = SimulatorCore(args.mode, reference, args.first_attempt, emit=log.emit)
    devices = {"input": getattr(args, "input", args.__dict__.get("wav_in")),
               "output": getattr(args, "output", args.__dict__.get("wav_out"))}
    log.emit({"event": "start", "t_utc": utc_now(), "mode": args.mode, "first_attempt": args.first_attempt,
              "devices": devices, "reference": reference, "block": cm.BLOCK, "rate": cm.FS,
              "output_gain_db": cm.OUTPUT_GAIN_DB,
              "condition": None if args.mode == "transparent" else cm.CONDITIONS[args.mode].__dict__})
    print(f"Simulator log: {folder}", flush=True)
    try:
        code = run_offline(args, core) if args.command == "offline" else run_audio(args, folder, core, log)
    finally:
        core.finish()
        if args.mode == "transparent":
            save_references(results, core.references_found, devices)
        log.write_status(core.status())
        log.emit({"event": "stop", "t_utc": utc_now(), "xruns_total": core.xruns})
        log.close()
    for d, ref in core.references_found.items():
        print(f"Reference {d}: tone {ref['tone_peak_dbfs']} dBFS peak, {ref['tone_s']} s", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
