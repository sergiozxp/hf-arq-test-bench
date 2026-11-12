"""Control of the channel simulator process from the GUI (no tkinter here, so it is testable)."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

MODES = {
    "Transparent (no fading, no noise)": "transparent",
    "Good: 0.5 ms, 0.1 Hz, +10 dB": "good",
    "Moderate: 1 ms, 0.5 Hz, +3 dB": "moderate",
    "Poor: 2 ms, 1 Hz, -3 dB": "poor",
}
LOG_RE = re.compile(r"Simulator log:\s*(.+?)\s*$")
ARMS = ("Mercury 1.9.19", "VARA HF unlicensed", "VARA HF licensed")
SNR_REFERENCES = {
    "Equal RMS SNR (primary)": "rms",
    "Peak-limited, PEP (secondary)": "peak",
}
MIN_POWER_S = {"A->B": 5.0, "B->A": 1.0}   # modem audio needed for a power reference


def run_command(input_device: str, output_device: str, mode: str, first_attempt: int,
                results: str = "results", arm: str | None = None, snr_reference: str = "rms") -> list[str]:
    command = ["channel_sim.py", "run", "--input", input_device, "--output", output_device,
               "--mode", mode, "--first-attempt", str(first_attempt), "--results", results,
               "--snr-reference", snr_reference]
    return command + (["--arm", arm] if arm else [])


def read_modem_power(results: Path) -> dict:
    try:
        return json.loads((results / "simulator" / "modem_power.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe_power(power: dict, arm: str) -> tuple[str, str]:
    entry = power.get(arm)
    if not entry:
        return (f"No RMS power reference for {arm} yet: run its Transparent smoke sequence, then press "
                "'Save as power reference'.", "orange")
    return (f"{arm} RMS power: A->B {entry['A->B']['rms_dbfs']} dBFS, B->A {entry['B->A']['rms_dbfs']} dBFS "
            f"(saved {entry.get('updated_utc', '?')[:16]} UTC).", "green")


def save_modem_power(results: Path, arm: str, status: dict | None, source: str = "") -> str:
    """Save the arm's power reference: its RMS in the first modem attempt (E0-1 16 B) of this
    Transparent run, i.e. at the modem's starting mode, the same for free and licensed VARA."""
    if not status or status.get("mode") != "transparent":
        raise ValueError("Start the simulator in Transparent mode and run this arm's smoke sequence first.")
    run = status.get("base_power") or {}
    for d, need in MIN_POWER_S.items():
        info = run.get(d) or {}
        if not info.get("p_rms") or info.get("active_s", 0) < need:
            raise ValueError(f"Not enough modem audio {d} in the first attempt ({info.get('active_s', 0)} s; "
                             f"at least {need:.0f} s). Restart the simulator and run the sequence again.")
    power = read_modem_power(results)
    power[arm] = {**{d: {k: run[d][k] for k in ("p_rms", "rms_dbfs", "active_s")} for d in MIN_POWER_S},
                  "updated_utc": status.get("t_utc"), "source": source}
    path = results / "simulator" / "modem_power.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(power, indent=2), encoding="utf-8")
    return describe_power(power, arm)[0]


def log_folder(console_text: str, base: Path) -> Path | None:
    for line in console_text.splitlines():
        match = LOG_RE.search(line)
        if match:
            folder = Path(match.group(1))
            return folder if folder.is_absolute() else base / folder
    return None


def read_status(folder: Path | None) -> dict | None:
    if folder is None:
        return None
    try:
        return json.loads((folder / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_reference(results: Path) -> dict | None:
    try:
        return json.loads((results / "simulator" / "reference.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe_reference(reference: dict | None) -> tuple[str, str]:
    if not reference or not all(d in reference.get("directions", {}) for d in ("A->B", "B->A")):
        return ("No reference yet: run the bench calibration (CAL) on both PCs with the simulator "
                "in Transparent mode.", "orange")
    dirs = reference["directions"]
    return ("Reference: A->B tone " f"{dirs['A->B']['tone_peak_dbfs']} dBFS peak, B->A "
            f"{dirs['B->A']['tone_peak_dbfs']} dBFS peak (updated {reference.get('updated_utc', '?')[:16]} UTC).",
            "green")


def describe_status(status: dict | None) -> tuple[str, str]:
    if not status:
        return "Simulator not running.", "gray"
    xruns = status.get("xruns_total") or {}
    dropouts = sum(xruns.values())
    parts = [f"Mode: {status.get('mode')}" + (f" ({status['snr_reference']} SNR, {status['arm']})"
                                             if status.get("arm") and status.get("mode") != "transparent" else ""),
             f"attempt {status['attempt']}" if status.get("attempt") is not None else "waiting for audio from PC A"]
    if status.get("seconds_since_audio") is not None:
        parts.append(f"last audio {status['seconds_since_audio']:.0f} s ago")
    parts.append(f"dropouts {dropouts}")
    problems = dropouts > 0
    for d, info in (status.get("directions") or {}).items():
        dev = info.get("peak_vs_reference_db")
        peak = info.get("input_peak_dbfs")
        if peak is not None and info.get("active_s", 0) > 0.2:
            parts.append(f"{d} peak {peak:.1f} dBFS" + (f" ({dev:+.1f} dB vs tone)" if dev is not None else ""))
            problems = problems or peak > -3.0
        if info.get("output_clipped_samples"):
            parts.append(f"{d} CLIPPING {info['output_clipped_samples']}")
            problems = True
    peaks = [(info or {}).get("input_peak_dbfs") for info in (status.get("directions") or {}).values()]
    if peaks and all(p is not None and p <= -89.0 for p in peaks):
        # 1 LSB on both inputs: the interface delivers digital silence, not even its noise floor
        # (2026-11-12: another program held UCA222-S). Real silence on a UCA222 reads near -80 dBFS.
        parts.append("NO SIGNAL AT ALL from UCA222-S input (digital silence): check that no other "
                     "program (VARA, Mercury) uses UCA222-S and that it is not muted")
        problems = True
    run = status.get("run_power") or {}
    if status.get("mode") == "transparent" and any((run.get(d) or {}).get("rms_dbfs") for d in run):
        base = status.get("base_power") or {}
        parts.append("power reference (first attempt): " + ", ".join(
            f"{d} {base[d]['rms_dbfs']} dBFS" for d in base if (base.get(d) or {}).get("rms_dbfs") is not None))
    found = status.get("references_found") or {}
    if found:
        parts.append("reference measured: " + ", ".join(sorted(found)))
    return " · ".join(parts), ("red" if problems else "green")


def stop(folder: Path | None, process: subprocess.Popen | None, wait_s: float = 8.0) -> int | None:
    """Ask the simulator to stop (STOP file), then terminate it if it does not."""
    if process is None:
        return None
    if folder is not None:
        try:
            (folder / "STOP").write_text("stop", encoding="utf-8")
        except OSError:
            pass
    deadline = time.monotonic() + wait_s
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return process.returncode
