"""VARA HF 4.9.0 support for the modem smoke GUI.

VARA is installed and started by the operator (it shows a licence dialog at start
and selects its sound card in its own window), so the GUI does not start it. This
module only inspects the installation:

- the SHA-256 of VARA.exe;
- the settings in VARA.ini that matter for the bench (sound devices, channel,
  TCP port, update check), read without ever returning a registration code;
- the licence state: registered or not, and the licensed callsigns (callsigns are
  not secret; the registration codes and the encryption password are).

Facts from the VARA HF 4.9.0 installation (VARA.ini and ChangeLog.txt):
TCP Command Port=8300 (data on 8301); Channel=Left|Right|L+R in the Soundcard menu;
the unregistered version is limited to 175 bps (level 4) with compression disabled.
"""
from __future__ import annotations

import configparser
import hashlib
from datetime import datetime, timezone
from pathlib import Path

VARA_VERSION = "4.9.0"
DEFAULT_EXE = Path(r"C:\VARA\VARA.exe")
# Official installer from downloads.winlink.org, recorded on 2026-10-22.
INSTALLER_ZIP_SHA256 = "5ad7d75c722e4414705dec998c28a711b7567f8568bea75cb84e8aa7c991f48a"
INSTALLER_EXE_SHA256 = "b7d67fd9fcc2043a49e58ae8202fc699bdb6520255d21aefd258260c767e04bd"
# VARA.exe of 4.9.0, used for the smoke tests of 2026-10-22/23. Any version may be used,
# as long as both PCs run the same binary (checked pair by pair and cell by cell).
VARA_490_EXE_SHA256 = "c69b8c81f888032a67f3c15fe57ef5ccdabef6eba5be148a5a4d7eb44f00486f"
PROCESS_NAME = "vara.exe"


class VaraError(Exception):
    """A condition that must stop the operator before VARA is used."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ini(path: str | Path) -> dict:
    """Return the bench-relevant VARA.ini settings. Never returns a registration code."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # keep VARA's key spelling
    parser.read_string(Path(path).read_bytes().decode("latin-1"))

    def get(section: str, key: str) -> str | None:
        return parser.get(section, key, fallback=None)

    codes = [value for key, value in (parser.items("Setup") if parser.has_section("Setup") else [])
             if key.lower().startswith("registration code") and value.strip()]
    callsigns = [value.strip() for key, value in
                 (parser.items("Setup") if parser.has_section("Setup") else [])
                 if key.lower().startswith("callsign licence") and value.strip()]
    port = get("Setup", "TCP Command Port")
    return {
        "registered": bool(codes),
        "licence_callsigns": callsigns,
        "input_device": get("Soundcard", "Input Device Name"),
        "output_device": get("Soundcard", "Output Device Name"),
        "channel": get("Soundcard", "Channel"),
        "drive_level_db": get("Soundcard", "ALC Drive Level"),
        "tcp_command_port": int(port) if port and port.strip().isdigit() else None,
        "update_check": get("Setup", "Updates"),
        "retries": get("Setup", "Retries"),
        "kiss_enabled": get("Setup", "Enable KISS"),
    }


def inspect(exe: str | Path) -> dict:
    """Hash VARA.exe and read the VARA.ini beside it."""
    exe = Path(exe)
    if not exe.is_file():
        raise VaraError(f"VARA.exe not found: {exe}")
    ini = exe.with_name("VARA.ini")
    if not ini.is_file():
        raise VaraError(f"VARA.ini not found beside VARA.exe ({ini}). Open VARA once and close it.")
    settings = read_ini(ini)
    return {"exe": str(exe), "exe_sha256": sha256_file(exe), "ini": str(ini),
            "registered": settings["registered"],
            "licence_callsigns": settings["licence_callsigns"],
            "settings": settings,
            "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def setting_notes(info: dict, mycall: str = "") -> tuple[list[str], list[str]]:
    """Return (errors, warnings) about the VARA settings for the bench."""
    errors, warnings = [], []
    if info.get("exe_sha256"):
        warnings.append("VARA.exe SHA-256 " + info["exe_sha256"][:12] + "…: the other PC must show the "
                        "same value (same VARA version on both PCs, update check off).")
    s = info["settings"]
    if s["tcp_command_port"] not in (None, 8300):
        errors.append(f"VARA uses TCP command port {s['tcp_command_port']}; the bench uses 8300.")
    if s["update_check"] == "1":
        warnings.append("VARA Setup: untick 'Allow VARA check for updates via internet' "
                        "so that version 4.9.0 does not change during the bench.")
    if s["kiss_enabled"] == "1":
        warnings.append("VARA Setup: KISS Interface is enabled; disable it for the bench.")
    for key, label in (("input_device", "input"), ("output_device", "output")):
        name = (s.get(key) or "").lower()
        if name and "uca222" not in name and "usb audio codec" not in name:
            warnings.append(f"VARA {label} device is '{s[key]}': check that it is the interface "
                            "connected to the other PC.")
    if info["registered"] and mycall:
        base = mycall.split("-", 1)[0].upper()
        if base not in {c.split("-", 1)[0].upper() for c in info["licence_callsigns"]}:
            warnings.append(f"VARA is registered for {info['licence_callsigns']}, not for {base}.")
    return errors, warnings


def owner_verdict(verdict: dict, owner_name: str | None) -> dict:
    """Refine a port preflight for VARA with the name of the process that owns 8300."""
    verdict = dict(verdict, owner_name=owner_name)
    if not verdict["ok"]:
        if not verdict["owner_pids"]:
            verdict["message"] = (
                "Nothing is listening on ports 8300 and 8301. Open VARA (press 'Continue to "
                "Evaluate' if the Free Trial window appears) and check that VARA Setup uses TCP "
                "command port 8300, then press Check VARA.")
        return verdict
    name = (owner_name or "").lower()
    if name == PROCESS_NAME:
        verdict.update(needs_confirmation=False,
                       message=f"VARA (PID {verdict['owner_pids'][0]}) is listening on 8300 and 8301.")
        return verdict
    if name:
        verdict.update(ok=False, needs_confirmation=False,
                       message=f"Ports 8300 and 8301 belong to {owner_name} (PID "
                               f"{verdict['owner_pids'][0]}), not to VARA. Close it and open VARA.")
        return verdict
    verdict.update(needs_confirmation=True,
                   message=f"Ports 8300 and 8301 belong to PID {verdict['owner_pids'][0]}; its "
                           "program name could not be read. Confirm that it is VARA.")
    return verdict
