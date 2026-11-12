"""Channel simulator for PC B (protocol v5.3, docs/phase1_channel_design.md).

UCA-S input L  (from PC A)  -> channel A->B -> UCA-S output L (to PC B's modem)
UCA-S input R  (from PC B)  -> channel B->A -> UCA-S output R (to PC A's modem)

Modes: transparent (output gain only), good / moderate / poor (Watterson + noise), or auto:
the condition, attempt number and arm of each attempt come from PC B, which writes
results/simulator/announce.json before it listens for an attempt ("listening") and when the
attempt has ended ("finished"). In auto mode an attempt starts at the first audio from PC A
after an announcement, and ends when PC B announces its end; audio while PC B announces no
attempt is not part of any attempt.

In transparent mode the simulator also measures the reference: when the bench
calibration tone (1500 Hz) arrives steadily for at least 3 s on an input, its power
is stored as P_ref for that direction (results/simulator/reference.json). The
channel modes use that reference and never adjust it during a run.

Attempts: the first A->B audio after at least SILENCE_S of silence starts attempt k;
both directions restart from attempt k's seeds. Each attempt is logged with its
input peak and mean power per direction, output clipping and audio dropouts.

USAGE
  python channel_sim.py list
  python channel_sim.py run --input <device> --output <device> --mode transparent|good|moderate|poor|auto
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
PEAK_TOLERANCE_DB = 1.0       # "peak" SNR reference only (secondary, PEP-limited profile)
PEAK_CEILING_DBFS = -3.0      # safety: a modem's peak at the simulator input must stay below this
ARMS = ("Mercury 1.9.19", "VARA HF unlicensed", "VARA HF licensed")
SNR_REFERENCES = ("rms", "peak")
AUTO = "auto"                 # follow PC B's announcement of each attempt (blocks, alpha.5)
ANNOUNCE_NAME = "announce.json"
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


def modem_power_path(results: Path) -> Path:
    return results / "simulator" / "modem_power.json"


def load_modem_power(results: Path) -> dict:
    try:
        return json.loads(modem_power_path(results).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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

    def __init__(self, mode: str, reference: dict | None, first_attempt: int = 1, emit=print,
                 snr_reference: str = "rms", arm: str | None = None, modem_power: dict | None = None):
        """snr_reference 'rms' (primary): noise from the arm's measured RMS power per direction,
        fixed for the run. 'peak' (secondary, PEP-limited): noise from the calibration tone."""
        if mode != "transparent" and ((mode not in cm.CONDITIONS and mode != AUTO) or reference is None):
            raise ValueError("a channel mode needs a known condition and a reference from the calibration")
        if snr_reference not in SNR_REFERENCES:
            raise ValueError(f"unknown SNR reference {snr_reference}")
        power = (modem_power or {}).get(arm or "", {})
        if mode in cm.CONDITIONS and snr_reference == "rms" and not all(d in power for d in CHANNELS):
            raise ValueError(f"no RMS power reference for '{arm}': run its Transparent smoke sequence and "
                             "save it as the arm's power reference")
        self.mode, self.reference, self.emit = mode, reference, emit
        self.snr_reference, self.arm = snr_reference, arm
        tone_refs = (reference or {}).get("directions", {})
        self.signal_ref = {d: (power[d]["p_rms"] if snr_reference == "rms" and d in power
                               else tone_refs.get(d, {}).get("p_ref", cm.reference_power()))
                           for d in CHANNELS}
        self.noise_power = ({d: self.signal_ref[d] / 10 ** (cm.CONDITIONS[mode].snr_db / 10) for d in CHANNELS}
                            if mode in cm.CONDITIONS else {})
        # cond: the channel condition of the current attempt. In Automatic mode it comes from PC B's
        # announcement of each attempt (condition, attempt number, arm), the run mode stays "auto".
        self.cond = None if mode == AUTO else mode
        self.modem_power = modem_power or {}
        self.entry: dict | None = None          # announcement of the current attempt (auto)
        self.announced: dict | None = None      # latest announcement seen (auto)
        self.pending: tuple | None = None       # (entry, paths, signal_ref, noise_power, problems)
        self.attempt_problems: list[str] = []
        self.stray_logged = False
        self.run_power = {d: [0.0, 0] for d in CHANNELS}     # completed attempts without the CAL tone
        # The arm's power reference is its RMS in the first modem attempt of a Transparent run
        # (the E0-1 16 B test: connection and a short transfer at the modem's starting mode).
        self.base_power: dict[str, list] | None = None
        self.attempt_tone = False
        self.attempt = first_attempt - 1
        self.started = False
        self.samples = 0
        self.last_active = -1e9
        self.paths = {d: cm.ChannelPath(mode if mode != AUTO else "transparent", d, self.signal_ref[d],
                                        first_attempt) for d in CHANNELS}
        self.stats = {d: DirectionStats() for d in CHANNELS}
        self.xruns = {"input_overflow": 0, "output_underflow": 0}
        self.attempt_xruns = dict(self.xruns)
        self.attempt_start = None
        self.tone = {d: [] for d in CHANNELS}
        self.references_found: dict[str, dict] = {}
        self.reference_count = 0

    # -- attempts
    def offer(self, entry: dict | None, modem_power: dict | None = None) -> None:
        """Automatic mode: PC B's announcement (results/simulator/announce.json), read by the control
        loop, never in the audio callback. A new 'listening' entry prepares the next attempt's
        channel here (filters and fading taps); the audio callback only swaps it in."""
        if self.mode != AUTO or not entry or entry == self.announced:
            return
        if modem_power is not None:
            self.modem_power = modem_power
        new = entry.get("state") == "listening" and entry.get("id") != (self.entry or {}).get("id") \
            and entry.get("id") != ((self.pending or (None,))[0] or {}).get("id")
        if new:
            cond, arm, k = entry.get("condition"), entry.get("arm"), int(entry.get("attempt") or 0)
            problems = []
            power = self.modem_power.get(arm or "", {})
            tone_refs = (self.reference or {}).get("directions", {})
            if cond not in cm.CONDITIONS or k < 1:
                problems.append(f"PC B announced an unknown condition or attempt ({cond}, {k})")
                cond = "transparent"
            if self.snr_reference == "rms" and not all(d in power for d in CHANNELS):
                problems.append(f"no RMS power reference for '{arm}'")
            ref = {d: (power[d]["p_rms"] if self.snr_reference == "rms" and d in power
                       else tone_refs.get(d, {}).get("p_ref", cm.reference_power())) for d in CHANNELS}
            if problems:
                cond = "transparent"
            paths = {d: cm.ChannelPath(cond, d, ref[d], max(k, 1)) for d in CHANNELS}
            noise = ({d: ref[d] / 10 ** (cm.CONDITIONS[cond].snr_db / 10) for d in CHANNELS}
                     if cond in cm.CONDITIONS else {})
            self.pending = (dict(entry), paths, ref, noise, problems)
        self.announced = dict(entry)
        self.emit({"event": "announcement", "t_utc": utc_now(), **entry})

    def _start_attempt(self, t_s: float) -> None:
        if self.started:
            self._end_attempt()
        self.attempt_problems = []
        if self.mode == AUTO:
            entry, self.paths, self.signal_ref, self.noise_power, self.attempt_problems = self.pending
            self.pending = None
            self.entry = entry
            self.cond = entry.get("condition") if not self.attempt_problems else "transparent"
            self.arm = entry.get("arm")
            self.attempt = int(entry.get("attempt") or 0)
        else:
            self.attempt += 1
        self.started = True
        self.stray_logged = False
        for d in CHANNELS:
            if self.mode != AUTO:
                self.paths[d].reset(self.attempt)
            self.stats[d] = DirectionStats()
        self.attempt_xruns = {k: 0 for k in self.xruns}
        self.attempt_tone = False
        self.attempt_start = utc_now()
        record = {"event": "attempt_start", "attempt": self.attempt, "t_utc": self.attempt_start,
                  "stream_s": round(t_s, 3), "mode": self.cond, "arm": self.arm,
                  "snr_reference": self.snr_reference,
                  "seeds": None if self.cond not in cm.CONDITIONS else
                  {d: [cm.SEED_ROOT, cm.CONDITIONS[self.cond].code, cm.DIRECTIONS[d], self.attempt]
                   for d in CHANNELS}}
        if self.mode == AUTO:
            record.update(auto=True, tag=self.entry.get("tag"), series=self.entry.get("series"),
                          announcement=self.entry.get("id"))
        self.emit(record)

    def _audio_after_silence(self, t_s: float) -> None:
        """Automatic mode: what audio from PC A after a silence (or the first audio) means."""
        pending = self.pending and self.pending[0].get("id") != (self.entry or {}).get("id")
        if pending:
            self._start_attempt(t_s)
            return
        current = self.started and self.announced and self.entry and \
            self.announced.get("id") == self.entry.get("id") and self.announced.get("state") == "listening"
        if current:
            self.emit({"event": "audio_resumed", "attempt": self.attempt, "t_utc": utc_now(),
                       "tag": self.entry.get("tag")})
            return
        if not self.stray_logged:
            self.emit({"event": "stray_audio", "t_utc": utc_now(),
                       "detail": "audio from PC A while PC B announced no attempt; not part of any attempt"})
            self.stray_logged = True

    def _refs(self) -> dict:
        """The saved reference, updated by any calibration tone measured in this run."""
        refs = dict((self.reference or {}).get("directions", {}))
        refs.update(self.references_found)
        return refs

    def _end_attempt(self) -> None:
        refs = self._refs()
        summary = {d: self.stats[d].summary(refs.get(d)) for d in CHANNELS}
        problems = list(self.attempt_problems)
        if any(self.attempt_xruns.values()):
            problems.append(f"audio dropouts {self.attempt_xruns}")
        for d in CHANNELS:
            st = self.stats[d]
            if not self.attempt_tone and st.active_blocks:
                self.run_power[d][0] += st.active_power
                self.run_power[d][1] += st.active_blocks
            if summary[d]["output_clipped_samples"]:
                problems.append(f"{d}: {summary[d]['output_clipped_samples']} clipped output samples")
            peak = summary[d]["input_peak_dbfs"]
            if peak is not None and peak > PEAK_CEILING_DBFS and summary[d]["active_s"] > 0.2:
                problems.append(f"{d}: modem peak {peak:.2f} dBFS above the {PEAK_CEILING_DBFS:.0f} dBFS ceiling")
            dev = summary[d].get("peak_vs_reference_db")
            if (self.snr_reference == "peak" and dev is not None and abs(dev) > PEAK_TOLERANCE_DB
                    and summary[d]["active_s"] > 1):
                problems.append(f"{d}: modem peak {dev:+.2f} dB from the reference")
            if self.noise_power and st.active_blocks:
                mean = st.active_power / st.active_blocks
                summary[d]["effective_snr_db"] = round(10 * math.log10(mean / self.noise_power[d]), 2)
        if self.base_power is None and not self.attempt_tone and self.stats["A->B"].active_blocks:
            self.base_power = {d: [self.stats[d].active_power, self.stats[d].active_blocks] for d in CHANNELS}
        record = {"event": "attempt_end", "attempt": self.attempt, "t_utc": utc_now(),
                  "started_utc": self.attempt_start, "mode": self.cond, "arm": self.arm,
                  "snr_reference": self.snr_reference, "calibration_tone": self.attempt_tone,
                  "directions": summary,
                  "xruns": dict(self.attempt_xruns), "valid": not problems, "problems": problems}
        if self.mode == AUTO and self.entry:
            record.update(auto=True, tag=self.entry.get("tag"), announcement=self.entry.get("id"))
        self.emit(record)

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
        self.attempt_tone = True
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
        if (self.mode == AUTO and self.started and self.announced and self.entry
                and self.announced.get("id") == self.entry.get("id") and self.announced.get("state") == "finished"):
            self._end_attempt()             # PC B has ended this attempt
            self.started = False
        rms_l = float(np.sqrt(np.mean(x[:, 0] ** 2)))
        if rms_l > 0 and 20 * math.log10(rms_l) > ACTIVE_DBFS:
            gap = t_s - self.last_active >= SILENCE_S
            if self.mode == AUTO:
                if gap or not self.started:
                    self._audio_after_silence(t_s)
            elif gap or not self.started:
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
        refs = self._refs()
        run = {}
        for d in CHANNELS:
            total, blocks = self.run_power[d]
            if not self.attempt_tone and self.stats[d].active_blocks:
                total, blocks = total + self.stats[d].active_power, blocks + self.stats[d].active_blocks
            run[d] = {"p_rms": total / blocks if blocks else None, "active_s": round(blocks * cm.BLOCK / cm.FS, 1),
                      "rms_dbfs": dbfs_power(total / blocks) if blocks else None}
        base_src = self.base_power
        if base_src is None and not self.attempt_tone and self.stats["A->B"].active_blocks:
            base_src = {d: [self.stats[d].active_power, self.stats[d].active_blocks] for d in CHANNELS}
        base = {d: {"p_rms": (base_src[d][0] / base_src[d][1]) if base_src and base_src[d][1] else None,
                    "active_s": round(base_src[d][1] * cm.BLOCK / cm.FS, 1) if base_src else 0.0,
                    "rms_dbfs": dbfs_power(base_src[d][0] / base_src[d][1])
                    if base_src and base_src[d][1] else None} for d in CHANNELS}
        return {"t_utc": utc_now(), "mode": self.mode, "condition": self.cond, "arm": self.arm,
                "snr_reference": self.snr_reference,
                "tag": (self.entry or {}).get("tag") if self.started else None,
                "announced": self.announced,
                "run_power": run, "base_power": base,
                "attempt": self.attempt if self.started else None,
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


def read_announcement(results: Path, not_before: datetime) -> dict | None:
    """PC B's latest announcement, if written after the simulator started."""
    try:
        entry = json.loads((results / "simulator" / ANNOUNCE_NAME).read_text(encoding="utf-8"))
        written = datetime.fromisoformat(entry["written_utc"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return entry if written >= not_before else None


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
        started = datetime.now(timezone.utc)
        while stream.active and not (folder / "STOP").exists():
            if args.mode == AUTO:
                entry = read_announcement(Path(args.results), started)
                if entry and entry != core.announced:
                    core.offer(entry, load_modem_power(Path(args.results)))
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
        p.add_argument("--mode", required=True, choices=["transparent", *cm.CONDITIONS, AUTO])
        p.add_argument("--first-attempt", type=int, default=1)
        p.add_argument("--results", default="results")
        p.add_argument("--snr-reference", choices=SNR_REFERENCES, default="rms",
                       help="rms: equal RMS SNR per arm (primary); peak: PEP-limited profile (secondary)")
        p.add_argument("--arm", choices=ARMS, default=None, help="the arm under test (its RMS power reference)")
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
    try:
        core = SimulatorCore(args.mode, reference, args.first_attempt, emit=log.emit,
                             snr_reference=args.snr_reference, arm=args.arm,
                             modem_power=load_modem_power(results))
    except ValueError as error:
        print(f"NO POWER REFERENCE: {error}", flush=True)
        log.emit({"event": "error", "detail": str(error), "t_utc": utc_now()})
        log.close()
        return 2
    devices = {"input": getattr(args, "input", args.__dict__.get("wav_in")),
               "output": getattr(args, "output", args.__dict__.get("wav_out"))}
    log.emit({"event": "start", "t_utc": utc_now(), "mode": args.mode, "first_attempt": args.first_attempt,
              "devices": devices, "reference": reference, "block": cm.BLOCK, "rate": cm.FS,
              "output_gain_db": cm.OUTPUT_GAIN_DB, "snr_reference": args.snr_reference, "arm": args.arm,
              "modem_power": load_modem_power(results).get(args.arm or ""),
              "condition": cm.CONDITIONS[args.mode].__dict__ if args.mode in cm.CONDITIONS else None})
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
