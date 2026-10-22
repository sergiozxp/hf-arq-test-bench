"""Unit tests for mercury_runtime (no Mercury, no sound card, no display)."""
from __future__ import annotations

import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import mercury_runtime as runtime
from smoke_common import listeners_with_pid


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


# Real output of "mercury.exe -x wasapi -z" (Mercury 1.9.19, Windows 11, PC A).
LISTING = """No configuration file found (mercury.ini): running on built-in defaults.  Use -C <file> to choose one; mercury.ini.example lists every setting.
playback devices:
device: name: 'Speakers (3- USB Audio Device)'  id: '{0.0.0.00000000}.{024a8463-64ec-4453-838d-5a2a198e82af}'
device: name: 'CABLE In 16ch (VB-Audio Virtual Cable)'  id: '{0.0.0.00000000}.{24114b01-3926-495a-bb04-cd3c88b66016}'
device: name: 'UCA222-A (USB Audio CODEC )'  id: '{0.0.0.00000000}.{555ff780-b392-436c-8d91-dfe00e74fe4b}'
device: name: 'CABLE Input (VB-Audio Virtual Cable)'  id: '{0.0.0.00000000}.{b89de593-f3a2-423c-92c2-8066dcd47652}'
device: name: 'Speakers (HyperX Quadcast)'  id: '{0.0.0.00000000}.{bda5f738-299c-4ac8-a6d2-98c431253ddd}'
device: name: 'Speakers (Realtek High Definition Audio)'  id: '{0.0.0.00000000}.{cce58f38-040b-43f1-b644-ae15ac8627d7}'  (system default)
capture devices:
device: name: 'Microphone (HyperX Quadcast)'  id: '{0.0.1.00000000}.{314bc6e0-939b-407e-a6e2-afbd7f286c84}'  (system default)
device: name: 'UCA222-A (USB Audio CODEC )'  id: '{0.0.1.00000000}.{70f71d9d-0b0a-4d67-b078-7a53b3879800}'
device: name: 'Microphone (3- USB Audio Device)'  id: '{0.0.1.00000000}.{7bfb864d-114b-46d6-b465-d1cf33d05515}'
device: name: 'CABLE Output (VB-Audio Virtual Cable)'  id: '{0.0.1.00000000}.{7c54aa91-9a3a-42d1-a237-64134400fc03}'
device: name: 'Microphone Array (Realtek High Definition Audio)'  id: '{0.0.1.00000000}.{b89ac8b6-7d9a-48f1-af34-9386e8d94517}'
"""

devices = runtime.parse_device_listing(LISTING.replace("\n", "\r\n"))
check(len(devices["playback"]) == 6 and len(devices["capture"]) == 5, "device listing is parsed (CRLF)")
uca_out = runtime.suggest_device(devices["playback"])
uca_in = runtime.suggest_device(devices["capture"])
check(uca_out and uca_out["id"].endswith("{555ff780-b392-436c-8d91-dfe00e74fe4b}"), "UCA222 output suggested")
check(uca_in and uca_in["id"].endswith("{70f71d9d-0b0a-4d67-b078-7a53b3879800}"), "UCA222 input suggested")
check(uca_out["name"] == "UCA222-A (USB Audio CODEC )", "device name keeps its trailing space")
check(devices["playback"][5]["system_default"] and not uca_out["system_default"], "system default is flagged")
two = devices["capture"] + [dict(uca_in, id="other")]
check(runtime.suggest_device(two) is None, "no suggestion when two devices look like a UCA222")
check(runtime.find_device(devices["capture"], uca_in["id"]) == uca_in, "device found by id")

ini = runtime.ini_text(uca_in["id"], uca_out["id"], "wasapi", "test")
check(f'input_device = "{uca_in["id"]}"' in ini, "input id is quoted in mercury.ini")
check("sound_system = wasapi" in ini and "[ptt]\nmethod = none" in ini, "sound system and PTT none written")
check("MYCALL" in ini and "mycall" not in ini.lower().split("[main]")[1], "callsign is not configured in the ini")
quoted = runtime.ini_text('a"b\\c', "x", "dsound", "test")
check('input_device = "a\\"b\\\\c"' in quoted, "quotes and backslashes are escaped like cfg_write")
for bad in (("", "x", "wasapi"), ("x", "y", "alsa")):
    try:
        runtime.ini_text(*bad, "test")
        check(False, f"ini_text rejects {bad}")
    except runtime.MercuryError:
        check(True, f"ini_text rejects {bad}")
check(runtime.launch_command("C:/m/mercury.exe", "C:/s/mercury.ini", "C:/s/mercury.log")
      == ["C:/m/mercury.exe", "-C", "C:/s/mercury.ini", "-L", "C:/s/mercury.log"], "launch command")

# netstat -ano: Spanish state text, IPv4 and IPv6 listeners, an established connection.
NETSTAT = """
Conexiones activas

  Proto  Dirección local          Dirección remota        Estado           PID
  TCP    0.0.0.0:135            0.0.0.0:0              ESCUCHANDO       1100
  TCP    0.0.0.0:8100           0.0.0.0:0              ESCUCHANDO       4242
  TCP    0.0.0.0:8300           0.0.0.0:0              ESCUCHANDO       4242
  TCP    0.0.0.0:8301           0.0.0.0:0              ESCUCHANDO       4242
  TCP    127.0.0.1:8300         127.0.0.1:50123        ESTABLECIDO      4242
  TCP    [::]:8300              [::]:0                 ESCUCHANDO       4242
  UDP    0.0.0.0:8300           *:*                                     999
"""
listeners = listeners_with_pid(NETSTAT, (8300, 8301))
check(len(listeners["8300"]) == 2 and listeners["8301"][0]["pid"] == 4242, "netstat listeners and PIDs parsed")
check(runtime.busy_ports(listeners_with_pid(NETSTAT, (8300, 8301, 8100))) ==
      {"8300": [4242], "8301": [4242], "8100": [4242]}, "busy ports reported with PIDs")
ok = runtime.preflight(listeners, 4242)
check(ok["ok"] and not ok["needs_confirmation"] and ok["all_interfaces"], "preflight passes for our Mercury")
check(not runtime.preflight(listeners, 7)["ok"], "preflight blocks a port owned by another PID")
external = runtime.preflight(listeners, None)
check(external["ok"] and external["needs_confirmation"], "external Mercury needs confirmation")
check(not runtime.preflight(listeners_with_pid("", (8300, 8301)), None)["ok"], "preflight blocks closed ports")
mixed = NETSTAT.replace("0.0.0.0:8301           0.0.0.0:0              ESCUCHANDO       4242",
                        "0.0.0.0:8301           0.0.0.0:0              ESCUCHANDO       5555")
check("different programs" in runtime.preflight(listeners_with_pid(mixed, (8300, 8301)), None)["message"],
      "preflight blocks ports split between programs")

# ZIP verification: a ZIP with the right layout but different bytes must be refused.
temporary = Path(tempfile.mkdtemp(prefix="hf_arq_runtime_"))
fake_zip = temporary / runtime.OFFICIAL_ZIP_NAME
with zipfile.ZipFile(fake_zip, "w") as archive:
    for name in runtime.OFFICIAL_FILES:
        archive.writestr(runtime.ZIP_ROOT + name, b"not mercury")
check(runtime.verify_zip(fake_zip)["official"] is False, "a lookalike ZIP is not official")
try:
    runtime.extract_official(fake_zip, temporary / "out")
    check(False, "lookalike ZIP is refused")
except runtime.MercuryError:
    check(True, "lookalike ZIP is refused")
check(not (temporary / "out" / "mercury.exe").exists(), "nothing is extracted from a refused ZIP")
check(set(runtime.extracted_state(temporary / "out").values()) == {"missing"}, "missing files reported")

# Process control with a stand-in program (not Mercury).
process = runtime.MercuryProcess()
console = temporary / "console.txt"
pid = process.start([sys.executable, "-c", "import time; print('hello', flush=True); time.sleep(60)"],
                    temporary, console)
time.sleep(1.0)
check(process.running() and process.pid == pid, "process starts and reports its PID")
try:
    process.start([sys.executable, "-c", "pass"], temporary, temporary / "second.txt")
    check(False, "a second start is refused")
except runtime.MercuryError:
    check(True, "a second start is refused")
code = process.stop()
check(not process.running() and code is not None, "process stops")
check("hello" in console.read_text(errors="replace"), "console output is captured to a file")
print("ALL RUNTIME TESTS PASSED")
