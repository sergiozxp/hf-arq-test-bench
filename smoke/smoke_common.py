"""Shared support for the MS-1.0 modem smoke scripts.

The module loads TOML configuration, creates one evidence directory per run,
captures environment metadata and raw TCP traffic, and creates deterministic
framed payloads. It never changes modem configuration or retries a test.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

HEADER_LEN = 16
# Child consoles stay hidden when the packaged GUI (which has no console) runs a tool.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A non-UTF-8 console must not interrupt the reader thread.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------- time and hashes
def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def mono_ns() -> int:
    # Python uses QueryPerformanceCounter on Windows.
    return time.perf_counter_ns()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# ---------------------------------------------------------------- configuration
def load_config(path: str) -> dict:
    p = Path(path)
    raw = p.read_bytes()
    cfg = tomllib.loads(raw.decode("utf-8"))
    cfg["_config_path"] = str(p.resolve())
    cfg["_config_sha256"] = sha256_bytes(raw)
    return cfg


# ------------------------------------------------------ payload y cabecera
def make_payload(size: int, seed: int) -> bytes:
    """Return deterministic high-entropy bytes for a fixed seed."""
    return random.Random(seed).randbytes(size)


def make_header(payload: bytes) -> bytes:
    """Build the common frame header: length plus truncated SHA-256."""
    return struct.pack(">Q", len(payload)) + hashlib.sha256(payload).digest()[:8]


def parse_header(h: bytes) -> tuple[int, bytes]:
    return struct.unpack(">Q", h[:8])[0], h[8:16]


# ---------------------------------------------------------------- run evidence
class RunLog:
    def __init__(self, cfg: dict, phase: str, tag: str):
        ahora = datetime.now(timezone.utc)
        self.run_id = f"{tag}_{ahora:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:6]}"
        base = Path(cfg.get("general", {}).get("results_dir", "results"))
        self.dir = base / phase / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._f = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        # Preserve the configuration copy that produced this run.
        shutil.copy(cfg["_config_path"], self.dir / "config_used.toml")
        self.log("start", run_id=self.run_id, config_sha256=cfg["_config_sha256"])

    def log(self, event: str, **data) -> None:
        rec = {"t_utc": utc_iso(), "t_mono_ns": mono_ns(), "event": event, **data}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self._f.write(line + "\n")
            self._f.flush()
        summary = json.dumps(data, ensure_ascii=False, default=str)
        print(f"[{rec['t_utc'][11:19]}] {event} {summary[:220]}", flush=True)

    def save_json(self, name: str, obj) -> Path:
        p = self.dir / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
        return p

    def close(self) -> None:
        with self._lock:
            self._f.close()


def ports_listening(salida: str, puertos) -> tuple[dict, dict]:
    """Detect listening TCP ports without depending on localized state text."""
    listening, lineas = {}, {}
    for p in puertos:
        m = re.search(rf"^\s*TCP\s+\S+:{p}\s+(?:0\.0\.0\.0:0|\[::\]:0|\*:\*)\s+\S+.*$",
                      salida, re.M | re.I)
        listening[str(p)] = bool(m)
        lineas[str(p)] = m.group(0).strip() if m else None
    return listening, lineas


LISTENER_RE = re.compile(
    r"^\s*TCP\s+(?P<local>\S+):(?P<port>\d+)\s+(?:0\.0\.0\.0:0|\[::\]:0|\*:\*)\s+\S+\s+(?P<pid>\d+)\s*$",
    re.M | re.I,
)


def listeners_with_pid(salida: str, puertos) -> dict[str, list[dict]]:
    """Return the listening sockets and owning PIDs for each port in netstat -ano output.

    Like ports_listening, the match does not depend on the localized state text
    ("LISTENING", "ESCUCHANDO", ...): a TCP line whose remote end is the wildcard
    address is a listener.
    """
    wanted = {str(p) for p in puertos}
    found: dict[str, list[dict]] = {p: [] for p in wanted}
    for m in LISTENER_RE.finditer(salida):
        if m.group("port") in wanted:
            found[m.group("port")].append({"address": m.group("local"), "pid": int(m.group("pid"))})
    return found


def netstat_text() -> str:
    """Return the output of netstat -ano, or an ERROR string."""
    return _run_command(["netstat", "-ano"])


def _run_command(comando: list[str], espera_s: int = 30) -> str:
    try:
        r = subprocess.run(comando, capture_output=True, text=True, errors="replace",
                           timeout=espera_s, creationflags=NO_WINDOW)
        return (r.stdout or "") + (("\nSTDERR: " + r.stderr) if r.stderr else "")
    except Exception as e:  # noqa: BLE001 - se registra el error tal cual
        return f"ERROR: {e!r}"


def capture_system() -> dict:
    """Collect best-effort power-plan and audio-endpoint metadata."""
    return {
        "power_plan": _run_command(["powercfg", "/getactivescheme"]).strip(),
        "audio_endpoints": _run_command(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class AudioEndpoint -PresentOnly | "
             "Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json"]).strip(),
    }


# VARA.ini keys whose values must never leave the PC. The registration code is a
# paid licence; the encryption password is a secret. Their values are replaced in
# every archived copy, and only whether a registration code was present is kept.
SECRET_INI_KEYS = re.compile(
    r"^(?P<key>\s*(?:Registration Code(?:\s*\d+)?|Password encryption)\s*=)(?P<value>[^\r\n]*)",
    re.I | re.M,
)
REDACTED = "<redacted by HF-ARQ Modem Smoke>"


def redact_ini_text(text: str) -> tuple[str, list[str], bool]:
    """Return (redacted text, redacted key names, registration code present)."""
    keys: list[str] = []
    registered = False

    def replace(match: re.Match) -> str:
        nonlocal registered
        value = match.group("value").strip()
        if not value:
            return match.group(0)
        name = match.group("key").split("=")[0].strip()
        keys.append(name)
        if name.lower().startswith("registration code"):
            registered = True
        return match.group("key") + REDACTED

    return SECRET_INI_KEYS.sub(replace, text), keys, registered


def archive_evidence_files(cfg: dict, rlog: RunLog, etapa: str) -> dict:
    """Copy configured evidence files and return hashes and source metadata.

    Files whose key starts with "vara_ini" are copied with their secret values
    redacted; the recorded SHA-256 is that of the redacted copy.
    """
    salida = {}
    destino = rlog.dir / "evidence" / etapa
    for nombre, ruta in cfg.get("evidence", {}).items():
        if not ruta:
            continue
        try:
            destino.mkdir(parents=True, exist_ok=True)
            # Prefix with the config key so equal basenames cannot collide.
            copia = destino / f"{nombre}__{Path(ruta).name}"
            origen = os.stat(ruta)
            extra = {}
            if nombre.startswith("vara_ini"):
                raw = Path(ruta).read_bytes().decode("latin-1")
                text, keys, registered = redact_ini_text(raw)
                copia.write_bytes(text.encode("latin-1"))
                extra = {"redacted_keys": keys, "registration_code_present": registered}
            else:
                shutil.copy(ruta, copia)
            # A live log is a snapshot; preserve its size and mtime when copied.
            salida[nombre] = {"ruta": ruta, "sha256": sha256_file(copia),
                              "bytes_origen": origen.st_size, "mtime_origen": origen.st_mtime,
                              "bytes_copia": copia.stat().st_size, **extra}
        except OSError as e:
            salida[nombre] = {"ruta": ruta, "error": repr(e)}
    return salida


def process_name(pid: int) -> str | None:
    """Return the image name of a Windows process (for example VARA.exe), or None."""
    if os.name != "nt":
        return None
    out = _run_command(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], 15)
    match = re.match(r'^\s*"([^"]+)","%d"' % int(pid), out)
    return match.group(1) if match else None


def tnc_config(cfg: dict) -> dict:
    """Return the TNC settings; [tnc] since 0.2.0-alpha.3, [mercury] before."""
    return cfg.get("tnc") or cfg["mercury"]


def evidence_issues(cfg: dict, env: dict, end: dict) -> dict:
    """Separate missing configured evidence from best-effort system warnings."""
    incompleta, adv = [], []
    for reg in (env.get("evidence_files_start", {}), end.get("evidence_files_end", {})):
        for nombre, d in reg.items():
            if isinstance(d, dict) and "error" in d:
                incompleta.append(f"{nombre}: {d['error']}")
    if not any(cfg.get("evidence", {}).values()):
        adv.append("[evidence] is empty; no Mercury configuration or log was archived")
    for k in ("power_plan", "audio_endpoints"):
        if str(env.get(k, "")).startswith("ERROR") or not str(env.get(k, "")).strip():
            adv.append(f"{k}: could not be collected ({str(env.get(k, ''))[:80]})")
    return {"incomplete_evidence": list(dict.fromkeys(incompleta)), "warnings": adv}


def capture_final(cfg: dict, rlog: RunLog, puertos=()) -> dict:
    """Capture final netstat output and a second evidence snapshot."""
    salida = _run_command(["netstat", "-ano"])
    (rlog.dir / "netstat_end.txt").write_text(salida, encoding="utf-8")
    esc, lin = ports_listening(salida, puertos)
    end = {"t_utc": utc_iso(), "listening": esc, "listening_lines": lin,
           "evidence_files_end": archive_evidence_files(cfg, rlog, "end")}
    rlog.save_json("environment_end.json", end)
    return end


def capture_environment(cfg: dict, rlog: RunLog, puertos=()) -> dict:
    env = {
        "t_utc": utc_iso(),
        "windows": platform.platform(),
        "windows_version": platform.version(),
        "python": sys.version,
        "config_path": cfg["_config_path"],
        "config_sha256": cfg["_config_sha256"],
        "binaries": {},
    }
    for nombre, ruta in cfg.get("binaries", {}).items():
        if not ruta:
            continue
        try:
            env["binaries"][nombre] = {"ruta": ruta, "sha256": sha256_file(ruta)}
        except OSError as e:
            env["binaries"][nombre] = {"ruta": ruta, "error": repr(e)}
    try:
        salida = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                errors="replace", timeout=30, creationflags=NO_WINDOW).stdout
    except Exception as e:  # noqa: BLE001 - se registra el error tal cual
        salida = f"ERROR: {e!r}"
    (rlog.dir / "netstat_start.txt").write_text(salida, encoding="utf-8")
    env["listening"], env["listening_lines"] = ports_listening(salida, puertos)
    env.update(capture_system())
    env["evidence_files_start"] = archive_evidence_files(cfg, rlog, "start")
    rlog.save_json("environment.json", env)
    rlog.log("environment", listening=env["listening"], binaries=env["binaries"])
    return env


# ------------------------------------------------------------------ TCP capture
class TcpCapture:
    """TCP connection with a reader thread and timestamped raw-byte captures."""

    def __init__(self, name: str, host: str, port: int, rlog: RunLog,
                 line_mode: bool):
        self.name, self.rlog, self.line_mode = name, rlog, line_mode
        self.cond = threading.Condition()
        self.buf = bytearray()
        self.lines: list[tuple[int, str]] = []
        self.chunks: list[tuple[int, int]] = []  # (t_mono_ns, received_bytes)
        self.closed = False
        self.error: str | None = None
        self._linebuf = bytearray()
        self._rx = open(rlog.dir / f"raw_rx_{name}.bin", "wb")
        self._tx = open(rlog.dir / f"raw_tx_{name}.bin", "wb")
        self._idx = open(rlog.dir / f"raw_rx_{name}.idx.jsonl", "w", encoding="utf-8")
        self._txidx = open(rlog.dir / f"raw_tx_{name}.idx.jsonl", "w", encoding="utf-8")
        self._tx_off = 0
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        rlog.log("socket_open", socket=name, host=host, port=port)

    # --- receive
    def _bucle(self) -> None:
        while not self._stop.is_set():
            try:
                datos = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self.error = repr(e)
                break
            if not datos:
                break
            t = mono_ns()
            self._rx.write(datos)
            self._rx.flush()
            self._idx.write(json.dumps({"t_mono_ns": t, "t_utc": utc_iso(),
                                        "n": len(datos)}) + "\n")
            self._idx.flush()
            with self.cond:
                self.chunks.append((t, len(datos)))
                if self.line_mode:
                    self._alimentar_lineas(datos, t)
                else:
                    self.buf.extend(datos)
                self.cond.notify_all()
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def _alimentar_lineas(self, datos: bytes, t: int) -> None:
        self._linebuf.extend(datos)
        partes = re.split(rb"[\r\n]+", bytes(self._linebuf))
        self._linebuf = bytearray(partes[-1])  # resto sin terminador
        for p in partes[:-1]:
            if p:
                texto = p.decode("utf-8", "replace")
                self.lines.append((t, texto))
                self.rlog.log("line_received", socket=self.name, text=texto)

    # --- send
    def send(self, data: bytes, label: str = "") -> None:
        t0, u0 = mono_ns(), utc_iso()
        self.sock.sendall(data)
        t1 = mono_ns()
        self._tx.write(data)
        self._tx.flush()
        # Index each sendall call with timestamps, stream offset, and byte count.
        self._txidx.write(json.dumps({"t_mono_ns": t0, "t_fin_ns": t1, "t_utc": u0,
                                      "offset": self._tx_off, "n": len(data),
                                      "label": label}) + "\n")
        self._txidx.flush()
        self._tx_off += len(data)
        self.rlog.log("tx", socket=self.name, label=label, bytes=len(data))

    def nlines(self) -> int:
        with self.cond:
            return len(self.lines)

    def command(self, texto: str, ventana_s: float) -> list[str]:
        """Send one CR-terminated command and return lines in its observation window."""
        start = self.nlines()
        self.send(texto.encode("ascii") + b"\r", label=texto)
        time.sleep(ventana_s)
        with self.cond:
            resp = [l[1] for l in self.lines[start:]]
        self.rlog.log("command_response", command=texto, lines=resp)
        return resp

    def wait_line(self, patron: str, timeout_s: float, start: int = 0):
        """Wait for a matching line, or return None on timeout or close."""
        rx = re.compile(patron)
        limite = time.monotonic() + timeout_s
        revisadas = start
        with self.cond:
            while True:
                for i in range(revisadas, len(self.lines)):
                    if rx.search(self.lines[i][1]):
                        return i, self.lines[i]
                revisadas = len(self.lines)
                resto = limite - time.monotonic()
                if self.closed or resto <= 0:
                    return None
                self.cond.wait(timeout=resto)

    def state(self) -> dict:
        """Return socket close and error state for result.json."""
        with self.cond:
            return {"closed": self.closed, "socket_error": self.error,
                    "received_bytes": sum(n for _, n in self.chunks)}

    def wait_line_after(self, patron: str, timeout_s: float, start: int, t_min_ns: int):
        """Like wait_line, but accept only a line after the supplied timestamp."""
        limite = time.monotonic() + timeout_s
        while True:
            r = self.wait_line(patron, max(0.0, limite - time.monotonic()), start=start)
            if r is None:
                return None
            if r[1][0] > t_min_ns:
                return r
            start = r[0] + 1

    def read_exact(self, n: int, idle_s: float) -> bytes:
        """Read exactly n bytes and reset the idle timer after every new byte."""
        limite = time.monotonic() + idle_s
        with self.cond:
            ultimo = len(self.buf)
            while len(self.buf) < n:
                if self.closed:
                    raise ConnectionError("socket cerrado antes de completar la lectura "
                                          f"(error del hilo lector: {self.error})")
                resto = limite - time.monotonic()
                if resto <= 0:
                    raise TimeoutError(f"{idle_s} s sin bytes nuevos "
                                       f"({len(self.buf)}/{n} recibidos)")
                self.cond.wait(timeout=resto)
                if len(self.buf) != ultimo:
                    ultimo = len(self.buf)
                    limite = time.monotonic() + idle_s
            salida = bytes(self.buf[:n])
            del self.buf[:n]
            return salida

    def pending(self) -> int:
        with self.cond:
            return len(self.buf)

    def close(self) -> None:
        self._stop.set()
        self._hilo.join(timeout=2)
        try:
            self.sock.close()
        except OSError:
            pass
        for f in (self._rx, self._tx, self._idx, self._txidx):
            f.close()

