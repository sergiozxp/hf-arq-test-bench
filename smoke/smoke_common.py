"""smoke_common.py - utilidades compartidas por los scripts del smoke test.

QUÉ HACE
  - Carga la configuración desde un archivo TOML y registra su SHA-256.
  - Crea una carpeta por corrida (results/<fase>/<run_id>/) con un registro
    de eventos (events.jsonl) con doble marca de tiempo (UTC y reloj monotónico).
  - Captura el entorno: versión de Windows y de Python, SHA-256 de los binarios
    configurados, salida de `netstat -ano` y si los puertos están en escucha.
  - Ofrece una captura TCP con hilo lector que guarda los bytes crudos recibidos
    (y los enviados) con su marca de tiempo.
  - Detecta puertos en escucha en `netstat` sin depender del idioma de Windows (LISTENING /
    ESCUCHANDO), y guarda netstat, plan de energía y dispositivos de audio al inicio y
    netstat al final. Copia con SHA-256 los archivos de la sección [registrar] del TOML.
  - Guarda un índice de tiempos también de los bytes ENVIADOS, y expone el estado/error
    de cada socket para distinguir cierre limpio, timeout y fallo de red.
  - Genera payloads deterministas (semilla fija, incompresibles) y la cabecera
    de aplicación de 16 bytes (longitud 8 B + SHA-256 truncado 8 B).

QUÉ NO HACE
  - No contiene lógica de prueba ni decide si un ensayo aprobó. Eso lo hace cada
    script, con los criterios del smoke test v6.
  - No modifica Mercury ni Pat, ni ningún archivo fuera de la carpeta de resultados.
  - No reintenta nada por su cuenta.
  - No interpreta el formato de las respuestas de Mercury: las guarda tal cual.

POR QUÉ
  - Una sola implementación del registro y de la cabecera evita que E0, H1 y H2
    midan con reglas distintas (requisito de reproducibilidad del banco v4).
  - Se guardan bytes crudos porque el formato exacto de respuestas y eventos aún
    está pendiente de confirmar en E0-2; no se debe perder evidencia por
    interpretarla de antemano.
  - El payload es determinista e incompresible para que el resultado no dependa
    de ninguna compresión de la aplicación.
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

# En Windows la consola puede no ser UTF-8: un carácter inesperado en una línea
# recibida no debe interrumpir el hilo lector ni la prueba.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------- tiempo y hash
def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def mono_ns() -> int:
    # time.perf_counter_ns usa QueryPerformanceCounter en Windows.
    return time.perf_counter_ns()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# ------------------------------------------------------------------ configuración
def load_config(path: str) -> dict:
    p = Path(path)
    raw = p.read_bytes()
    cfg = tomllib.loads(raw.decode("utf-8"))
    cfg["_config_path"] = str(p.resolve())
    cfg["_config_sha256"] = sha256_bytes(raw)
    return cfg


# ------------------------------------------------------ payload y cabecera
def make_payload(size: int, seed: int) -> bytes:
    """Bytes deterministas e incompresibles (misma semilla, mismo archivo)."""
    return random.Random(seed).randbytes(size)


def make_header(payload: bytes) -> bytes:
    """Cabecera de aplicación (nuestra, no de Mercury): longitud (8 B, big-endian)
    seguida de los primeros 8 bytes del SHA-256 del payload."""
    return struct.pack(">Q", len(payload)) + hashlib.sha256(payload).digest()[:8]


def parse_header(h: bytes) -> tuple[int, bytes]:
    return struct.unpack(">Q", h[:8])[0], h[8:16]


# --------------------------------------------------------------- registro de corrida
class RunLog:
    def __init__(self, cfg: dict, phase: str, tag: str):
        ahora = datetime.now(timezone.utc)
        self.run_id = f"{tag}_{ahora:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:6]}"
        base = Path(cfg.get("general", {}).get("results_dir", "results"))
        self.dir = base / phase / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._f = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        # Copia de la configuración usada, con su hash.
        shutil.copy(cfg["_config_path"], self.dir / "config_usada.toml")
        self.log("inicio", run_id=self.run_id, config_sha256=cfg["_config_sha256"])

    def log(self, tipo: str, **datos) -> None:
        rec = {"t_utc": utc_iso(), "t_mono_ns": mono_ns(), "tipo": tipo, **datos}
        linea = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self._f.write(linea + "\n")
            self._f.flush()
        resumen = json.dumps(datos, ensure_ascii=False, default=str)
        print(f"[{rec['t_utc'][11:19]}] {tipo} {resumen[:220]}", flush=True)

    def save_json(self, nombre: str, obj) -> Path:
        p = self.dir / nombre
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
        return p

    def close(self) -> None:
        with self._lock:
            self._f.close()


def puertos_en_escucha(salida: str, puertos) -> tuple[dict, dict]:
    """Detecta puertos TCP en escucha SIN depender del idioma de Windows: el estado
    se escribe LISTENING en inglés pero ESCUCHANDO en español, así que se reconoce por
    la dirección remota vacía (0.0.0.0:0, [::]:0 o *:*) y no por la palabra del estado."""
    escuchando, lineas = {}, {}
    for p in puertos:
        m = re.search(rf"^\s*TCP\s+\S+:{p}\s+(?:0\.0\.0\.0:0|\[::\]:0|\*:\*)\s+\S+.*$",
                      salida, re.M | re.I)
        escuchando[str(p)] = bool(m)
        lineas[str(p)] = m.group(0).strip() if m else None
    return escuchando, lineas


def _ejecutar(comando: list[str], espera_s: int = 30) -> str:
    try:
        r = subprocess.run(comando, capture_output=True, text=True, errors="replace",
                           timeout=espera_s)
        return (r.stdout or "") + (("\nSTDERR: " + r.stderr) if r.stderr else "")
    except Exception as e:  # noqa: BLE001 - se registra el error tal cual
        return f"ERROR: {e!r}"


def capturas_sistema() -> dict:
    """Mejor esfuerzo: plan de energía y dispositivos de audio. Si falla, se registra el
    error; el nivel de audio y las mejoras de Windows se anotan a mano en la planilla."""
    return {
        "plan_energia": _ejecutar(["powercfg", "/getactivescheme"]).strip(),
        "audio_endpoints": _ejecutar(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class AudioEndpoint -PresentOnly | "
             "Select-Object FriendlyName,Status,InstanceId | ConvertTo-Json"]).strip(),
    }


def registrar_archivos(cfg: dict, rlog: RunLog, etapa: str) -> dict:
    """Copia a results/.../registro/<etapa>/ los archivos de la sección [registrar]
    (mercury.ini, configuración y logs de Pat y de Mercury) y guarda su SHA-256."""
    salida = {}
    destino = rlog.dir / "registro" / etapa
    for nombre, ruta in cfg.get("registrar", {}).items():
        if not ruta:
            continue
        try:
            destino.mkdir(parents=True, exist_ok=True)
            # La clave va en el nombre: dos archivos distintos con el mismo nombre no se pisan.
            copia = destino / f"{nombre}__{Path(ruta).name}"
            origen = os.stat(ruta)
            shutil.copy(ruta, copia)
            # Un log vivo es una INSTANTÁNEA: se anotan tamaño y fecha del origen al copiar.
            salida[nombre] = {"ruta": ruta, "sha256": sha256_file(copia),
                              "bytes_origen": origen.st_size, "mtime_origen": origen.st_mtime,
                              "bytes_copia": copia.stat().st_size}
        except OSError as e:
            salida[nombre] = {"ruta": ruta, "error": repr(e)}
    return salida


def problemas_de_evidencia(cfg: dict, env: dict, fin: dict) -> dict:
    """Separa lo que invalida la evidencia de lo que solo es una advertencia.
    - evidencia_incompleta: un archivo de [registrar] SÍ configurado no se pudo copiar
      (v6 exige guardar mercury.ini, configuración y logs con su SHA-256).
    - advertencias: [registrar] sin ningún archivo, o fallo de powercfg/audio (mejor esfuerzo)."""
    incompleta, adv = [], []
    for reg in (env.get("archivos_registrados_inicio", {}), fin.get("archivos_registrados_fin", {})):
        for nombre, d in reg.items():
            if isinstance(d, dict) and "error" in d:
                incompleta.append(f"{nombre}: {d['error']}")
    if not any(cfg.get("registrar", {}).values()):
        adv.append("[registrar] vacío: no se registraron mercury.ini, configuración de Pat ni "
                   "logs (v6 los exige para el criterio formal)")
    for k in ("plan_energia", "audio_endpoints"):
        if str(env.get(k, "")).startswith("ERROR") or not str(env.get(k, "")).strip():
            adv.append(f"{k}: no se pudo obtener ({str(env.get(k, ''))[:80]})")
    return {"evidencia_incompleta": list(dict.fromkeys(incompleta)), "advertencias": adv}


def capture_final(cfg: dict, rlog: RunLog, puertos=()) -> dict:
    """Estado al terminar: netstat final y de nuevo los archivos de [registrar]."""
    salida = _ejecutar(["netstat", "-ano"])
    (rlog.dir / "netstat_fin.txt").write_text(salida, encoding="utf-8")
    esc, lin = puertos_en_escucha(salida, puertos)
    fin = {"t_utc": utc_iso(), "escuchando": esc, "escuchando_lineas": lin,
           "archivos_registrados_fin": registrar_archivos(cfg, rlog, "fin")}
    rlog.save_json("environment_fin.json", fin)
    return fin


def capture_environment(cfg: dict, rlog: RunLog, puertos=()) -> dict:
    env = {
        "t_utc": utc_iso(),
        "windows": platform.platform(),
        "version_windows": platform.version(),
        "python": sys.version,
        "config_ruta": cfg["_config_path"],
        "config_sha256": cfg["_config_sha256"],
        "binarios": {},
    }
    for nombre, ruta in cfg.get("binaries", {}).items():
        if not ruta:
            continue
        try:
            env["binarios"][nombre] = {"ruta": ruta, "sha256": sha256_file(ruta)}
        except OSError as e:
            env["binarios"][nombre] = {"ruta": ruta, "error": repr(e)}
    try:
        salida = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                errors="replace", timeout=30).stdout
    except Exception as e:  # noqa: BLE001 - se registra el error tal cual
        salida = f"ERROR: {e!r}"
    (rlog.dir / "netstat_inicio.txt").write_text(salida, encoding="utf-8")
    env["escuchando"], env["escuchando_lineas"] = puertos_en_escucha(salida, puertos)
    env.update(capturas_sistema())
    env["archivos_registrados_inicio"] = registrar_archivos(cfg, rlog, "inicio")
    rlog.save_json("environment.json", env)
    rlog.log("entorno", escuchando=env["escuchando"], binarios=env["binarios"])
    return env


# ------------------------------------------------------------------ captura TCP
class TcpCapture:
    """Conexión TCP con hilo lector. Guarda bytes crudos y su índice de tiempos.

    line_mode=True  -> separa líneas (terminadas en \\r o \\n): socket de comandos.
    line_mode=False -> acumula bytes en un búfer: socket de datos.
    """

    def __init__(self, nombre: str, host: str, puerto: int, rlog: RunLog,
                 line_mode: bool):
        self.nombre, self.rlog, self.line_mode = nombre, rlog, line_mode
        self.cond = threading.Condition()
        self.buf = bytearray()
        self.lines: list[tuple[int, str]] = []
        self.chunks: list[tuple[int, int]] = []  # (t_mono_ns, n_bytes) por recv
        self.closed = False
        self.error: str | None = None
        self._linebuf = bytearray()
        self._rx = open(rlog.dir / f"raw_rx_{nombre}.bin", "wb")
        self._tx = open(rlog.dir / f"raw_tx_{nombre}.bin", "wb")
        self._idx = open(rlog.dir / f"raw_rx_{nombre}.idx.jsonl", "w", encoding="utf-8")
        self._txidx = open(rlog.dir / f"raw_tx_{nombre}.idx.jsonl", "w", encoding="utf-8")
        self._tx_off = 0
        self.sock = socket.create_connection((host, puerto), timeout=10)
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        rlog.log("socket_abierto", socket=nombre, host=host, puerto=puerto)

    # --- lectura
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
                self.rlog.log("linea_rx", socket=self.nombre, texto=texto)

    # --- envío
    def send(self, datos: bytes, etiqueta: str = "") -> None:
        t0, u0 = mono_ns(), utc_iso()
        self.sock.sendall(datos)
        t1 = mono_ns()
        self._tx.write(datos)
        self._tx.flush()
        # Un registro por envío: instante antes y después de sendall, posición y longitud.
        self._txidx.write(json.dumps({"t_mono_ns": t0, "t_fin_ns": t1, "t_utc": u0,
                                      "offset": self._tx_off, "n": len(datos),
                                      "etiqueta": etiqueta}) + "\n")
        self._txidx.flush()
        self._tx_off += len(datos)
        self.rlog.log("tx", socket=self.nombre, etiqueta=etiqueta, bytes=len(datos))

    def nlines(self) -> int:
        with self.cond:
            return len(self.lines)

    def command(self, texto: str, ventana_s: float) -> list[str]:
        """Envía un comando (terminado en \\r) y devuelve las líneas recibidas en la
        ventana. Los eventos asíncronos que caigan en la ventana también se listan."""
        inicio = self.nlines()
        self.send(texto.encode("ascii") + b"\r", etiqueta=texto)
        time.sleep(ventana_s)
        with self.cond:
            resp = [l[1] for l in self.lines[inicio:]]
        self.rlog.log("respuesta_comando", comando=texto, lineas=resp)
        return resp

    def wait_line(self, patron: str, timeout_s: float, start: int = 0):
        """Espera una línea que cumpla la expresión regular. Devuelve
        (indice, (t_mono_ns, texto)) o None si vence el tiempo o se cierra el socket."""
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

    def estado(self) -> dict:
        """Cierre limpio, cierre con error del socket, o abierto (para result.json)."""
        with self.cond:
            return {"cerrado": self.closed, "error_socket": self.error,
                    "bytes_recibidos": sum(n for _, n in self.chunks)}

    def wait_line_after(self, patron: str, timeout_s: float, start: int, t_min_ns: int):
        """Como wait_line, pero solo acepta una línea cuya marca monotónica sea
        ESTRICTAMENTE posterior a t_min_ns (descarta eventos viejos que el hilo lector
        procesó tarde)."""
        limite = time.monotonic() + timeout_s
        while True:
            r = self.wait_line(patron, max(0.0, limite - time.monotonic()), start=start)
            if r is None:
                return None
            if r[1][0] > t_min_ns:
                return r
            start = r[0] + 1

    def read_exact(self, n: int, idle_s: float) -> bytes:
        """Lee exactamente n bytes. El plazo se renueva con cada byte nuevo
        (60 s sin bytes nuevos = timeout, como en E0-1)."""
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
