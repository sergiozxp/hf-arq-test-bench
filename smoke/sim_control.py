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


def run_command(input_device: str, output_device: str, mode: str, first_attempt: int,
                results: str = "results") -> list[str]:
    return ["channel_sim.py", "run", "--input", input_device, "--output", output_device,
            "--mode", mode, "--first-attempt", str(first_attempt), "--results", results]


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
    parts = [f"Mode: {status.get('mode')}",
             f"attempt {status['attempt']}" if status.get("attempt") is not None else "waiting for audio from PC A"]
    if status.get("seconds_since_audio") is not None:
        parts.append(f"last audio {status['seconds_since_audio']:.0f} s ago")
    parts.append(f"dropouts {dropouts}")
    problems = dropouts > 0
    for d, info in (status.get("directions") or {}).items():
        dev = info.get("peak_vs_reference_db")
        if dev is not None and info.get("active_s", 0) > 1:
            parts.append(f"{d} peak {dev:+.1f} dB vs reference")
            problems = problems or abs(dev) > 1.0
        if info.get("output_clipped_samples"):
            parts.append(f"{d} CLIPPING {info['output_clipped_samples']}")
            problems = True
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
