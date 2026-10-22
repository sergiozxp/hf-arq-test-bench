"""Mercury 1.9.19 runtime support for the modem smoke GUI.

The GUI uses this module to run Mercury from the official release ZIP:

- verify the ZIP against the SHA-256 published for the 1.9.19 release, and
  every extracted file against the hash of the same file inside that ZIP;
- list sound devices with Mercury's own enumeration (mercury.exe -x <api> -z),
  so the selected id is exactly the one Mercury will open;
- write a minimal mercury.ini that selects the chosen devices;
- start and stop mercury.exe and record exactly how it was started;
- check which process listens on ports 8300 and 8301 before a test.

It never modifies the ZIP, never changes Windows audio settings, and never
treats a running Mercury as proof of anything: the smoke scripts decide
pass or fail.

Facts taken from the Mercury 1.9.19 source (tag v1.9.19, commit e9f0072):
-i and -o accept the exact id printed by -z (a unique device name also works),
-C selects the INI file, -L writes the log, the audio engine opens devices at
48 kHz, and the ARQ TCP ports bind to all interfaces (INADDR_ANY).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MERCURY_VERSION = "1.9.19"
OFFICIAL_RELEASE_URL = "https://github.com/Rhizomatica/mercury/releases/tag/v1.9.19"
OFFICIAL_ZIP_NAME = "mercury-1.9.19-w64-e9f0072d.zip"
OFFICIAL_ZIP_SHA256 = "6b0e83185d5052d01e0d3041c9c6588023f9a0abe0196c62563dc33ce4467673"
ZIP_ROOT = "mercury-1.9.19/"
# Files needed by the headless modem, with their SHA-256 inside the official ZIP.
# mercury-ui.exe is deliberately not extracted: it starts its own engine and
# would compete with mercury.exe for the sound card and the TCP ports.
OFFICIAL_FILES = {
    "mercury.exe": "c0fab18b49033846d688b56043b82e8e58f760b72be452239d98cbae93fa3a58",
    "libgcc_s_seh-1.dll": "172cfdc9d83703ec0a0ca2cee08f6cf2a166b801717ee51a56b3a63b36bc2f83",
    "libwinpthread-1.dll": "fc3cd567190f3f24e8201c2e1602f7d1f1e583a572efb7dbd8746d7928538e94",
    "libhamlib-4.dll": "6677cd7f2c4702a7e8a4c45f360bda3c6ef89e1e8a2a51ace13ebdcefe191dea",
    "libusb-1.0.dll": "86d5d599c427d5c168bea98f24584520be6187df142dd0ff1def4a834848a0a2",
}
SOUND_SYSTEMS = ("wasapi", "dsound")
CMD_PORT = 8300
DATA_PORT = 8301
BROADCAST_PORT = 8100
ALL_INTERFACES = ("0.0.0.0", "[::]", "*")


class MercuryError(Exception):
    """A condition that must stop the operator before Mercury is used."""


# ------------------------------------------------------------------ hashing and ZIP
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_zip(path: str | Path) -> dict:
    """Hash a ZIP and compare it with the official 1.9.19 Windows ZIP."""
    path = Path(path)
    digest = sha256_file(path)
    return {
        "path": str(path),
        "file_name": path.name,
        "sha256": digest,
        "official_sha256": OFFICIAL_ZIP_SHA256,
        "official": digest == OFFICIAL_ZIP_SHA256,
        "name_matches": path.name == OFFICIAL_ZIP_NAME,
    }


def extracted_state(dest: str | Path) -> dict:
    """Return the hash state of each required file in an extraction folder."""
    dest = Path(dest)
    state = {}
    for name, expected in OFFICIAL_FILES.items():
        target = dest / name
        if not target.is_file():
            state[name] = "missing"
        else:
            state[name] = "ok" if sha256_file(target) == expected else "modified"
    return state


def extract_official(zip_path: str | Path, dest: str | Path) -> dict:
    """Extract the headless Mercury files from the verified official ZIP.

    Files are written under fixed names (never under names taken from the
    archive) and each one is checked against its official hash, so a modified
    archive or a modified extraction cannot be used.
    """
    check = verify_zip(zip_path)
    if not check["official"]:
        raise MercuryError(
            "This ZIP is not the official Mercury 1.9.19 Windows ZIP.\n"
            f"Expected SHA-256: {OFFICIAL_ZIP_SHA256}\nFound SHA-256:    {check['sha256']}"
        )
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    actions = {}
    with zipfile.ZipFile(zip_path) as archive:
        for name, expected in OFFICIAL_FILES.items():
            data = archive.read(ZIP_ROOT + name)
            if sha256_bytes(data) != expected:
                raise MercuryError(f"{name} inside the ZIP does not have its official hash.")
            target = dest / name
            if target.is_file() and sha256_file(target) == expected:
                actions[name] = "kept"
                continue
            actions[name] = "replaced" if target.exists() else "extracted"
            target.write_bytes(data)
    state = extracted_state(dest)
    if any(value != "ok" for value in state.values()):
        raise MercuryError(f"Extraction could not be verified: {state}")
    return {"zip": check, "folder": str(dest), "exe": str(dest / "mercury.exe"),
            "files": dict(OFFICIAL_FILES), "actions": actions}


# ------------------------------------------------------------------ sound devices
DEVICE_LINE_RE = re.compile(
    r"^device: name: '(?P<name>.*)'\s+id: '(?P<id>[^']*)'(?P<default>\s+\(system default\))?\s*$"
)


def parse_device_listing(text: str) -> dict[str, list[dict]]:
    """Parse the output of mercury.exe -z into playback and capture lists."""
    devices: dict[str, list[dict]] = {"playback": [], "capture": []}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if lowered.startswith("playback devices"):
            section = "playback"
            continue
        if lowered.startswith("capture devices"):
            section = "capture"
            continue
        match = DEVICE_LINE_RE.match(line)
        if match and section:
            devices[section].append({
                "name": match.group("name"),
                "id": match.group("id"),
                "system_default": bool(match.group("default")),
            })
    return devices


SUGGESTION_WORDS = ("uca222", "usb audio codec")


def suggest_device(devices: list[dict]) -> dict | None:
    """Return the only device that looks like a UCA222, or None if not unique."""
    candidates = [d for d in devices if any(w in d["name"].lower() for w in SUGGESTION_WORDS)]
    return candidates[0] if len(candidates) == 1 else None


def find_device(devices: list[dict], device_id: str) -> dict | None:
    return next((d for d in devices if d["id"] == device_id), None)


def run_tool(command: list[str], cwd: str | Path, timeout_s: float = 30) -> str:
    """Run a short Mercury query and return stdout plus stderr."""
    result = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, errors="replace",
        timeout=timeout_s, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    return (result.stdout or "") + (result.stderr or "")


def list_devices(exe: str | Path, sound_system: str) -> tuple[str, dict[str, list[dict]]]:
    if sound_system not in SOUND_SYSTEMS:
        raise MercuryError(f"Unsupported sound system: {sound_system}")
    exe = Path(exe)
    text = run_tool([str(exe), "-x", sound_system, "-z"], exe.parent)
    return text, parse_device_listing(text)


def version_text(exe: str | Path) -> str:
    exe = Path(exe)
    return run_tool([str(exe), "-V"], exe.parent, timeout_s=15).strip()


# ------------------------------------------------------------------ configuration
def _ini_quote(value: str) -> str:
    """Quote a value the way Mercury's own cfg_write does (escape \\ and \")."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ini_text(input_id: str, output_id: str, sound_system: str, app_version: str) -> str:
    """Return a minimal mercury.ini. Unlisted settings keep Mercury's defaults."""
    if sound_system not in SOUND_SYSTEMS:
        raise MercuryError(f"Unsupported sound system: {sound_system}")
    if not input_id or not output_id:
        raise MercuryError("Select both an input and an output device.")
    return (
        f"; Generated by HF-ARQ Modem Smoke {app_version} for profile MS-1.0.\n"
        f"; Mercury {MERCURY_VERSION}. Settings not listed keep Mercury's built-in defaults.\n"
        "; The callsign and bandwidth are not set here: the smoke client sends\n"
        "; MYCALL and BW500/BW2300 on port 8300 at the start of every test.\n"
        "[main]\n"
        "ui_enabled = false\n"
        f"input_device = {_ini_quote(input_id)}\n"
        f"output_device = {_ini_quote(output_id)}\n"
        "capture_channel = left\n"
        f"sound_system = {sound_system}\n"
        f"arq_tcp_base_port = {CMD_PORT}\n"
        f"broadcast_tcp_port = {BROADCAST_PORT}\n"
        "verbose = false\n"
        "\n"
        "[ptt]\n"
        "method = none\n"
    )


def launch_command(exe: str | Path, ini_path: str | Path, log_path: str | Path) -> list[str]:
    return [str(exe), "-C", str(ini_path), "-L", str(log_path)]


def new_session_dir(results_dir: str | Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(results_dir) / "mercury_sessions" / f"{stamp}_{uuid.uuid4().hex[:6]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: str | Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                          encoding="utf-8")


# ------------------------------------------------------------------ process control
class MercuryProcess:
    """One mercury.exe started by the GUI, with its console output in a file."""

    def __init__(self):
        self.popen: subprocess.Popen | None = None
        self._console = None
        self.command: list[str] = []

    def start(self, command: list[str], cwd: str | Path, console_path: str | Path) -> int:
        if self.running():
            raise MercuryError("Mercury is already running from this window.")
        self._console = open(console_path, "wb")
        self.command = list(command)
        try:
            self.popen = subprocess.Popen(
                self.command, cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=self._console, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
            )
        except OSError:
            self._console.close()
            self._console = None
            raise
        return self.popen.pid

    @property
    def pid(self) -> int | None:
        return self.popen.pid if self.popen else None

    def running(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def exit_code(self) -> int | None:
        return None if self.popen is None else self.popen.poll()

    def stop(self, timeout_s: float = 10) -> int | None:
        """Terminate Mercury. Windows offers no clean signal to a hidden console."""
        if self.popen is None:
            return None
        if self.popen.poll() is None:
            self.popen.terminate()
            try:
                self.popen.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait(timeout=timeout_s)
        if self._console:
            self._console.close()
            self._console = None
        return self.popen.returncode


# ------------------------------------------------------------------ port checks
def busy_ports(listeners: dict[str, list[dict]]) -> dict[str, list[int]]:
    """Return the ports that already have a listener, with their PIDs."""
    return {port: sorted({item["pid"] for item in items})
            for port, items in listeners.items() if items}


def preflight(listeners: dict[str, list[dict]], mercury_pid: int | None) -> dict:
    """Decide whether a smoke test may start, given who listens on 8300 and 8301.

    Returns ok (may start), needs_confirmation (Mercury was not started from
    this window, so its command line is not recorded), a message for the
    operator, the owning PIDs, and whether the ports are open on all interfaces.
    """
    result = {"ok": False, "needs_confirmation": False, "message": "",
              "owner_pids": [], "all_interfaces": False, "listeners": listeners}
    missing = [port for port in (str(CMD_PORT), str(DATA_PORT)) if not listeners.get(port)]
    if missing:
        result["message"] = (
            f"Nothing is listening on port {' and '.join(missing)}. Start Mercury and "
            "wait until both 8300 and 8301 are listening."
        )
        return result
    pids = sorted({item["pid"] for port in (str(CMD_PORT), str(DATA_PORT))
                   for item in listeners[port]})
    result["owner_pids"] = pids
    result["all_interfaces"] = any(item["address"] in ALL_INTERFACES
                                   for items in listeners.values() for item in items)
    if len(pids) > 1:
        result["message"] = (
            f"Ports 8300 and 8301 belong to different programs (PIDs {pids}). Close every "
            "other modem program (for example VARA or a second Mercury) and try again."
        )
        return result
    if mercury_pid is not None and pids != [mercury_pid]:
        result["message"] = (
            f"Port 8300 belongs to PID {pids[0]}, not to the Mercury started here "
            f"(PID {mercury_pid}). Close the other program and try again."
        )
        return result
    result["ok"] = True
    if mercury_pid is None:
        result["needs_confirmation"] = True
        result["message"] = (
            f"Ports 8300 and 8301 belong to PID {pids[0]}, which was not started from this "
            "window. The test can run, but the Mercury command line, configuration and log "
            "will not be recorded automatically."
        )
    else:
        result["message"] = f"Mercury (PID {mercury_pid}) is listening on 8300 and 8301."
    return result
