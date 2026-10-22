"""Integration test against the real, official Mercury 1.9.19 Windows ZIP.

Usage: python tests/test_real_mercury.py --zip <mercury-1.9.19-w64-e9f0072d.zip>

On any system it checks the ZIP hash and the verified extraction. On Windows it
also runs the real mercury.exe: -V, the device listing, and a headless start
with the generated mercury.ini and the "null" sound system (no sound card is
needed), then checks that Mercury owns ports 8300 and 8301 and stops it.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import mercury_runtime as runtime
import mercury_status
from smoke_common import listeners_with_pid, netstat_text, process_name


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True)
    args = parser.parse_args()
    temporary = Path(tempfile.mkdtemp(prefix="hf_arq_real_"))
    folder = temporary / "mercury-1.9.19"

    check(runtime.verify_zip(args.zip)["official"], "the ZIP has the official 1.9.19 SHA-256")
    first = runtime.extract_official(args.zip, folder)
    check(set(first["actions"].values()) == {"extracted"}, "files extracted on first use")
    check(not (folder / "mercury-ui.exe").exists(), "mercury-ui.exe is not extracted")
    check(set(runtime.extract_official(args.zip, folder)["actions"].values()) == {"kept"},
          "verified files are reused")
    exe = folder / "mercury.exe"
    exe.write_bytes(exe.read_bytes()[:-1] + b"\0")
    check(runtime.extracted_state(folder)["mercury.exe"] == "modified", "a modified mercury.exe is detected")
    check(runtime.extract_official(args.zip, folder)["actions"]["mercury.exe"] == "replaced",
          "a modified mercury.exe is replaced from the ZIP")

    if os.name != "nt":
        print("SKIP running mercury.exe: not Windows")
        print("REAL MERCURY TESTS PASSED (ZIP only)")
        return 0

    version = runtime.version_text(exe)
    print(version)
    check(runtime.MERCURY_VERSION in version, "mercury.exe -V reports 1.9.19")
    listing, devices = runtime.list_devices(exe, "wasapi")
    print(listing)
    print(f"devices: {len(devices['playback'])} playback, {len(devices['capture'])} capture")

    session = temporary / "session"
    session.mkdir()
    ini = session / "mercury.ini"
    # The generated INI, with the sound system switched to "null" for a runner without audio.
    text = runtime.ini_text("{0.0.1.00000000}.{00000000-0000-0000-0000-000000000000}",
                           "{0.0.0.00000000}.{00000000-0000-0000-0000-000000000000}", "wasapi", "ci")
    ini.write_text(text.replace("sound_system = wasapi", "sound_system = null"), encoding="utf-8")
    process = runtime.MercuryProcess()
    pid = process.start([str(exe), "-C", str(ini), "-L", str(session / "mercury.log")],
                        session, session / "mercury_console.txt")
    verdict = None
    monitor = None
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and process.running():
            verdict = runtime.preflight(listeners_with_pid(netstat_text(), (8300, 8301)), pid)
            if verdict["ok"]:
                break
            time.sleep(1)
        check(verdict is not None and verdict["ok"], f"Mercury (PID {pid}) owns 8300 and 8301")
        check((process_name(pid) or "").lower() == "mercury.exe", "the port owner is identified as mercury.exe")
        monitor = mercury_status.StatusMonitor(runtime.UI_PORT, log_path=session / "status.jsonl").start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and monitor.latest is None:
            time.sleep(0.5)
        print("status:", monitor.latest, "error:", monitor.error)
        check(monitor.latest is not None, "Mercury's status WebSocket delivers status messages")
        check(isinstance(monitor.latest.get("audio_ok"), bool)
              and isinstance(monitor.latest.get("tx_peak_dbfs"), (int, float)),
              "status carries the meter fields (audio_ok, tx_peak_dbfs)")
    finally:
        if monitor:
            monitor.stop()
        code = process.stop()
        print("--- mercury_console.txt ---")
        print((session / "mercury_console.txt").read_text(errors="replace")[-3000:])
    print(f"Mercury stopped with code {code}; listeners: {verdict['listeners']}")
    print("REAL MERCURY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
