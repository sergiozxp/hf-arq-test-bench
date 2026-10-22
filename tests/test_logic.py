"""Unit tests for the Mercury-only GUI logic."""
from __future__ import annotations

import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import smoke_gui_logic as logic
from smoke_common import load_config


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message)
    if not condition:
        raise SystemExit(1)


values = {
    "role": "A",
    "mycall": " xq2cg ",
    "peer_call": "ea5abc",
    "bw": "BW500",
    "mercury_zip": r"C:\Users\Ana Perez\Downloads\mercury.zip",
    "mercury_exe": r"C:\mercury\mercury.exe",
}
check(logic.validate_form(values) == [] and values["mycall"] == "XQ2CG", "valid form normalizes callsign")
check(
    len(logic.validate_form(dict(values, peer_call="XQ2CG"))) == 1,
    "identical callsigns are rejected",
)
check(
    len(logic.validate_form(dict(values, mycall="", role="Z"))) == 2,
    "missing callsign and invalid role are rejected",
)

config = tomllib.loads(logic.config_text(values))
check(config["general"]["protocol_version"] == "5.0", "protocol version is written")
check(config["general"]["smoke_profile"] == "MS-1.0", "smoke profile is written")
check("pat" not in config and "evidence" in config, "configuration is Mercury-only")
check(config["binaries"]["mercury_zip"] == values["mercury_zip"], "Windows path is preserved")
check(config["general"]["app_version"] == logic.APP_VERSION, "application version is written")
check(config["mercury_session"] == {"launched_by_gui": False}, "external Mercury is recorded as such")

session = {
    "launched_by_gui": True, "pid": 4242, "sound_system": "wasapi",
    "input_name": "UCA222-A (USB Audio CODEC )", "input_id": "{0.0.1.00000000}.{70f71d9d}",
    "command": [r"C:\HF ARQ\mercury-1.9.19\mercury.exe", "-C", r"C:\x\mercury.ini"],
    "version_output": "Mercury 1.9.19\n(e9f0072)", "exe": r"C:\HF ARQ\mercury.exe",
    "ini": r"C:\x\mercury.ini", "log": r"C:\x\mercury.log", "console": r"C:\x\console.txt",
}
with_session = tomllib.loads(logic.config_text(values, session))
check(with_session["mercury_session"]["pid"] == 4242 and
      with_session["mercury_session"]["command"][0] == session["command"][0], "Mercury session is recorded")
check(with_session["binaries"]["mercury_exe"] == session["exe"] and
      with_session["evidence"]["mercury_console"] == session["console"], "session evidence paths are recorded")
check(not any("V3" in name for name in logic.MERCURY_TESTS), "E0-2 V3 is no longer offered")
check(logic.is_h1(logic.H1_TEST) and not logic.is_h1("E0-1  16-byte payload"), "only H1 uses the repetition number")
check("repetition number" in logic.REPETITION_LABEL, "repetition field is labelled as a number")

config_path = Path(tempfile.gettempdir()) / "hf_arq_logic_test.toml"
config_path.write_text(logic.config_text(values), encoding="utf-8")
loaded = load_config(str(config_path))
check(loaded["mercury"]["cmd_port"] == 8300, "generated TOML loads in smoke code")

command = logic.mercury_commands(
    logic.MERCURY_TESTS["H1  Formal 200-byte transfer"], "B", 2
)[0]
check(
    command == [
        "mercury_smoke_client.py", "listen", "--tag", "H1-2",
        "--phase", "smoke", "--payload-size", "200", "--bw-when", "before",
    ],
    "H1 receiver command is correct",
)
check(
    logic.result_path_from_line("Result saved to: results/e0/X/result.json")
    == "results/e0/X/result.json",
    "result path is parsed",
)
check(logic.summarize({"mode": "send", "sender_completed": True})[1] == "orange", "sender remains provisional")
check(logic.summarize({"mode": "listen", "delivered_intact": True})[1] == "green", "receiver hash is green")
check(
    logic.summarize({"sender_receiver_hash_matches": True, "outcome": "passed"})[1] == "green",
    "passing cross-check is green",
)
check(logic.run_script("not_a_script.py", []) == 2, "unknown bundled script is rejected")
print("ALL LOGIC TESTS PASSED")
