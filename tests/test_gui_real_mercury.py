"""Drive the real GUI (real Tk) against the real Mercury 1.9.19, on Windows only.

Usage: python tests/test_gui_real_mercury.py --zip <mercury-1.9.19-w64-e9f0072d.zip>

A CI runner has no sound card, so two things are substituted: the device list
comes from a fixture, and the generated mercury.ini selects Mercury's "null"
sound system. Everything else is real: ZIP verification and extraction, the
Mercury process started by the GUI, the port and PID check, the session
record, and a receiver test that sends MYCALL, BW500 and LISTEN ON to Mercury
and must reach 'waiting_for_connection'.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True)
    args = parser.parse_args()
    if os.name != "nt":
        print("SKIP: this test runs the real mercury.exe and needs Windows")
        return 0

    import mercury_runtime as runtime
    import smoke_gui

    work = Path(tempfile.mkdtemp(prefix="hf_arq_gui_"))
    smoke_gui.HERE = work
    smoke_gui.RESULTS = work / "results"
    smoke_gui.SETTINGS = work / "settings.json"
    smoke_gui.MERCURY_DIR = work / "mercury-1.9.19"

    dialogs: list[tuple[str, tuple]] = []
    for name in ("showwarning", "showinfo", "showerror"):
        setattr(smoke_gui.messagebox, name,
                lambda *a, _name=name, **k: dialogs.append((_name, a)))
    smoke_gui.messagebox.askyesno = lambda *a, **k: True
    smoke_gui.messagebox.askyesnocancel = lambda *a, **k: True

    fixture = {
        "playback": [{"name": "UCA222-A (USB Audio CODEC )", "id": "{0.0.0.00000000}.{out}",
                      "system_default": False}],
        "capture": [{"name": "UCA222-A (USB Audio CODEC )", "id": "{0.0.1.00000000}.{in}",
                     "system_default": False}],
    }
    runtime.list_devices = lambda exe, sound_system: ("fixture listing", fixture)
    original_ini = runtime.ini_text
    runtime.ini_text = lambda *a: original_ini(*a).replace("sound_system = wasapi", "sound_system = null")

    window = smoke_gui.ModemSmokeWindow()
    window.withdraw()

    def pump(condition, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            window.update()
            if condition():
                return True
            time.sleep(0.05)
        return False

    def log_text() -> str:
        return window.log.get("1.0", "end")

    try:
        window.values["mercury_zip"].set(args.zip)
        window._prepare_zip(quiet=False)
        check(pump(lambda: window.mercury_exe and window.labels["capture"], 90),
              "ZIP verified, mercury.exe extracted and devices listed")
        check("verified" in window.zip_status.cget("text"), "ZIP status reports verification")
        window._use_suggestion()
        check(window.values["input_id"].get() == "{0.0.1.00000000}.{in}" and
              window.values["output_id"].get() == "{0.0.0.00000000}.{out}", "suggested devices selected")

        window._start_mercury()
        check(pump(lambda: "listening on 8300 and 8301" in window.mercury_status.cget("text"), 60),
              "GUI started Mercury and it owns 8300/8301: " + window.mercury_status.cget("text"))
        session_dir = Path(window.session["session_dir"])
        recorded = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        check(recorded["pid"] == window.mercury.pid and recorded["zip_official"], "session.json records PID and ZIP")
        check(runtime.MERCURY_VERSION in recorded["version_output"], "version output recorded")

        window.values["role"].set("B")
        window.values["mycall"].set("XQ2CG-B")
        window.values["peer_call"].set("XQ2CG-A")
        window.test_name.set("E0-1  16-byte payload")
        window._run()
        check(pump(lambda: "waiting_for_connection" in log_text(), 90),
              "receiver reached waiting_for_connection (MYCALL, BW500 and LISTEN ON accepted)")
        config = tomllib.loads((work / "smoke_config.toml").read_text(encoding="utf-8"))
        check(config["mercury_session"]["launched_by_gui"] is True and
              config["mercury_session"]["pid"] == window.mercury.pid, "test configuration records the Mercury session")
        window._stop()
        check(pump(lambda: not window._test_running(), 120), "receiver test stopped")

        window._stop_mercury()
        check(not window.mercury.running(), "GUI stopped Mercury")
        ended = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        check(ended.get("stop_reason") == "stopped_by_operator", "session end recorded")
    finally:
        print("--- GUI log ---")
        print(log_text())
        print("--- dialogs ---", dialogs)
        if window.mercury.running():
            window.mercury.stop()
        window.destroy()
    print("GUI + REAL MERCURY TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
