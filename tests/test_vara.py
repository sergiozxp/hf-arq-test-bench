"""VARA support tests: VARA.ini reading, secret redaction in the evidence, owner checks."""
from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "smoke"))
import smoke_common
import smoke_gui_logic as logic
import vara_runtime as vara


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


SECRET = "2z5SECRETCODE987"
# VARA.ini as written by VARA HF 4.9.0 (CRLF), registered and with an encryption password.
INI = (
    "[Soundcard]\r\nInput Device Name=UCA222-A (USB Audio CODEC )\r\n"
    "Output Device Name=UCA222-A (USB Audio CODEC )\r\nALC Drive Level=-5\r\nChannel=1\r\n"
    "[Setup]\r\nRegistration Code=" + SECRET + "\r\nRegistration Code 1=\r\n"
    "Registration Code 2=\r\nRegistration Code 3=\r\nCallsign Licence 0=XQ2CG\r\n"
    "Callsign Licence 1=\r\nTCP Command Port=8300\r\nRetries=15\r\nUpdates=0\r\n"
    "Password encryption=MyPass\r\nEnable KISS=0\r\n"
)

folder = Path(tempfile.mkdtemp(prefix="hf_arq_vara_"))
exe = folder / "VARA.exe"
exe.write_bytes(b"MZ not really VARA")
ini = folder / "VARA.ini"
ini.write_bytes(INI.encode("latin-1"))

info = vara.inspect(exe)
check(info["registered"] is True and info["licence_callsigns"] == ["XQ2CG"], "licence state is read")
check(SECRET not in json.dumps(info), "the registration code is never returned")
check(info["settings"]["tcp_command_port"] == 8300 and info["settings"]["channel"] == "1", "settings are read")
errors, warnings = vara.setting_notes(info, "XQ2CG-1")
check(errors == [] and warnings == [], "a clean registered setup has no notes")
_, warnings = vara.setting_notes(info, "EA5ABC-1")
check(any("registered for" in w for w in warnings), "a callsign outside the licence is flagged")

unregistered = INI.replace(SECRET, "").replace("Updates=0", "Updates=1").replace("TCP Command Port=8300",
                                                                                 "TCP Command Port=8500")
ini.write_bytes(unregistered.encode("latin-1"))
info2 = vara.inspect(exe)
errors, warnings = vara.setting_notes(info2)
check(info2["registered"] is False, "an empty code means unregistered")
check(errors and "8500" in errors[0], "a different TCP port is an error")
check(any("updates" in w for w in warnings), "the update check is flagged")
ini.write_bytes(INI.encode("latin-1"))

# The archived evidence never contains the code; CRLF line endings survive.
values = {"mycall": "XQ2CG-1", "peer_call": "XQ2CG-2", "bw": "BW500", "modem": "vara", "role": "A"}
config = folder / "smoke_config.toml"
config.write_text(logic.config_text(values, None, info).replace('results_dir = "results"',
                  f"results_dir = {json.dumps(str(folder / 'results'))}"), encoding="utf-8")
cfg = smoke_common.load_config(str(config))
check(SECRET not in config.read_text(encoding="utf-8"), "the configuration does not contain the code")
check(cfg["modem"]["arm"] == "VARA HF licensed", "the arm is VARA HF licensed")
rlog = smoke_common.RunLog(cfg, "e0", "TEST")
archived = smoke_common.archive_evidence_files(cfg, rlog, "start")["vara_ini"]
copy = next((rlog.dir / "evidence" / "start").glob("vara_ini__*"))
text = copy.read_bytes().decode("latin-1")
check(SECRET not in text and "MyPass" not in text, "the archived VARA.ini has no code and no password")
check("Registration Code=<redacted" in text and "\r\nTCP Command Port=8300\r\n" in text,
      "the redacted copy keeps every other line and CRLF")
check(archived["registration_code_present"] is True and
      archived["redacted_keys"] == ["Registration Code", "Password encryption"], "redaction is recorded")
check(SECRET not in json.dumps(archived), "the evidence record has no code")
rlog.close()
for path in (folder / "results").rglob("*"):
    if path.is_file():
        check(SECRET.encode() not in path.read_bytes(), f"no code in {path.name}")

# Port owner rules.
ok = {"ok": True, "needs_confirmation": True, "message": "", "owner_pids": [7], "all_interfaces": True,
      "listeners": {}}
check(vara.owner_verdict(ok, "VARA.exe")["ok"] and not vara.owner_verdict(ok, "VARA.exe")["needs_confirmation"],
      "VARA owning the ports is ready")
check(not vara.owner_verdict(ok, "mercury.exe")["ok"], "Mercury owning the ports blocks a VARA test")
check(vara.owner_verdict(ok, None)["needs_confirmation"], "an unknown owner needs confirmation")
check(not vara.owner_verdict(dict(ok, ok=False), "VARA.exe")["ok"], "closed ports stay blocked")
print("ALL VARA TESTS PASSED")
