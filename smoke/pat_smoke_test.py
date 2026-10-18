"""pat_smoke_test.py - pruebas por la API HTTP de Pat 1.0.0 (E0-3, E0-4 y H2).

Cada subcomando es un paso, para ejecutarlo a mano en el PC que corresponda
y poder registrar cada paso por separado.

FLUJO E0-3 / H2 (adjunto)
  PC A:  python pat_smoke_test.py queue --size 200 --tag E0-3_200B
         -> crea state.json con el UUID de corrida y el SHA-256 del adjunto
  PC A:  python pat_smoke_test.py connect --tag E0-3_200B --call CALL-B
  (copiar state.json de A a B, por la red de control o USB: no es payload)
  PC B:  python pat_smoke_test.py check-in --tag E0-3_200B --state state.json

FLUJO E0-4 (ACK en sesión de retorno; requiere E0-3 aprobado)
  PC B:  python pat_smoke_test.py ack-queue --tag E0-4 --to CALL-A \
              --uuid <UUID> --sha256 <SHA-256 completo recibido>
  PC B:  python pat_smoke_test.py connect --tag E0-4 --call CALL-A
  PC A:  python pat_smoke_test.py ack-verify --tag E0-4 --state state.json
  Entre repeticiones de H2:  python pat_smoke_test.py disconnect --tag H2-x

QUÉ HACE
  - Crea el mensaje con adjunto (POST /api/mailbox/out, multipart: to, subject=UUID,
    body, files), confirma que está en la bandeja de salida y guarda el SHA-256.
  - Llama POST /api/connect?url=varahf:///<CALL> y registra código HTTP, cuerpo
    completo y duración.
  - En el receptor lee GET /api/mailbox/in, identifica el mensaje por el UUID del
    asunto, descarga cada adjunto y compara su SHA-256 con el del envío.
  - Genera el ACK (asunto `ACK run=<UUID>`, cuerpo con el UUID y el SHA-256 completos)
    y lo verifica en A.
  - Guarda cada petición y respuesta HTTP (código y cuerpo íntegros).

QUÉ NO HACE
  - No inicia ni reinicia Pat ni Mercury, ni modifica su configuración (varahf.addr,
    varahf.bandwidth, listen): esa configuración se fija a mano y se registra.
  - No reintenta llamadas. Un HTTP 500 en /api/connect se registra como fallo de sesión.
  - No usa NumReceived como prueba de entrega: solo cuenta mensajes nuevos en la
    bandeja de entrada de quien llama, no que el mensaje haya llegado al otro PC.
  - No usa el estado de la bandeja de salida como criterio (es solo observación).
  - No envía el ACK dentro de la misma sesión de datos (eso sería un caso nuevo).

POR QUÉ
  - Un paso por subcomando permite ver y archivar la evidencia de cada etapa y evita
    que un fallo quede oculto dentro de un flujo automático.
  - Los nombres de campos del JSON de Pat (MID, Subject, Files, Name, Body) no están
    verificados contra una respuesta real; el lector es tolerante a mayúsculas y
    guarda la respuesta cruda. Si no encuentra el mensaje, lo dice y deja la
    evidencia para diagnosticar, en lugar de adivinar.
  - Los endpoints y campos (to, subject, files, body) vienen del código fuente de Pat
    1.0.0, según el documento del smoke test, pero no están probados en ejecución.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from smoke_common import (RunLog, capture_environment, load_config, make_payload,
                          mono_ns, sha256_bytes)


# ------------------------------------------------------------------ HTTP
class Cliente:
    def __init__(self, cfg: dict, rlog: RunLog):
        self.base = cfg["pat"]["base_url"].rstrip("/")
        self.rlog = rlog
        self.n = 0

    def llamar(self, metodo: str, ruta: str, etiqueta: str, *, datos: bytes | None = None,
               tipo: str | None = None, timeout: float = 30) -> tuple[int | None, bytes]:
        self.n += 1
        url = self.base + ruta
        req = urllib.request.Request(url, data=datos, method=metodo)
        if tipo:
            req.add_header("Content-Type", tipo)
        t0 = mono_ns()
        status: int | None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, cuerpo = r.status, r.read()
        except urllib.error.HTTPError as e:   # 4xx/5xx: se registra, no se oculta
            status, cuerpo = e.code, e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status, cuerpo = None, repr(e).encode()
        dur = (mono_ns() - t0) / 1e9
        (self.rlog.dir / f"http_{self.n:02d}_{etiqueta}.body").write_bytes(cuerpo)
        self.rlog.save_json(f"http_{self.n:02d}_{etiqueta}.json", {
            "metodo": metodo, "url": url, "codigo_http": status, "duracion_s": dur,
            "bytes_cuerpo": len(cuerpo),
            "cuerpo_texto": cuerpo.decode("utf-8", "replace")[:200000]})
        self.rlog.log("http", etiqueta=etiqueta, metodo=metodo, url=url,
                      codigo=status, duracion_s=round(dur, 3), bytes=len(cuerpo))
        return status, cuerpo


def multipart(campos: dict[str, str], archivos: list[tuple[str, str, bytes]]):
    borde = "----smoke" + uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f'--{borde}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                      f"{v}\r\n".encode("utf-8"))
    for campo, nombre, contenido in archivos:
        partes.append((f'--{borde}\r\nContent-Disposition: form-data; name="{campo}"; '
                       f'filename="{nombre}"\r\nContent-Type: application/octet-stream'
                       "\r\n\r\n").encode("utf-8") + contenido + b"\r\n")
    partes.append(f"--{borde}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={borde}"


# ------------------------------------------- lectura tolerante del JSON de Pat
def getk(d: dict, *nombres):
    bajos = {k.lower(): v for k, v in d.items()}
    for n in nombres:
        if n.lower() in bajos:
            return bajos[n.lower()]
    return None


def como_lista(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                return v
    return []


def parsear(cuerpo: bytes):
    try:
        return json.loads(cuerpo.decode("utf-8", "replace"))
    except ValueError:
        return None


def buscar_mensajes(lista: list, fragmento: str) -> list[dict]:
    return [m for m in lista if isinstance(m, dict)
            and fragmento in str(getk(m, "Subject", "subject") or "")]


# ------------------------------------------------------------ subcomandos
def cmd_queue(a, cfg, rlog, cli):
    p = cfg["pat"]
    run_uuid = str(uuid.uuid4())
    contenido = make_payload(a.size, cfg["general"]["payload_seed"])
    nombre = f"smoke_{a.size}B_{run_uuid[:8]}.bin"
    (rlog.dir / nombre).write_bytes(contenido)
    sha = sha256_bytes(contenido)
    cuerpo, tipo = multipart(
        {"to": p["peer_call"], "subject": run_uuid, "body": f"smoke run {run_uuid}"},
        [("files", nombre, contenido)])
    st, resp = cli.llamar("POST", "/api/mailbox/out", "post_out", datos=cuerpo, tipo=tipo,
                          timeout=p["http_timeout_s"])
    st2, resp2 = cli.llamar("GET", "/api/mailbox/out", "get_out", timeout=p["http_timeout_s"])
    en_salida = st2 is not None and run_uuid in resp2.decode("utf-8", "replace")
    estado = {"run_uuid": run_uuid, "asunto": run_uuid, "adjunto": nombre, "bytes": a.size,
              "adjunto_sha256": sha, "origen": p["mycall"], "destino": p["peer_call"],
              "codigo_post_out": st, "en_bandeja_salida": en_salida}
    ruta_estado = rlog.save_json("state.json", estado)
    # Se imprime la ruta completa: este archivo se copia al PC B para check-in.
    print(f"\nstate.json (copiar al PC B para check-in): {ruta_estado.resolve()}", flush=True)
    ok = st is not None and 200 <= st < 300 and en_salida
    return {"paso": "queue", **estado, "resultado": "ok" if ok else "fallo"}


def cmd_connect(a, cfg, rlog, cli):
    p = cfg["pat"]
    url = f"{p['transport']}:///{a.call}"
    st, resp = cli.llamar("POST", "/api/connect?url=" + quote(url, safe=":/"), "post_connect",
                          datos=b"", timeout=p["connect_timeout_s"])
    # HTTP 200 NO prueba entrega; HTTP 500 = fallo de sesión.
    return {"paso": "connect", "url": url, "codigo_http": st,
            "cuerpo": resp.decode("utf-8", "replace")[:5000],
            "sesion": "ok_http" if st == 200 else "fallo",
            "nota": "HTTP 200/NumReceived no prueba entrega; verificar en la bandeja de B"}


def cmd_checkin(a, cfg, rlog, cli):
    p = cfg["pat"]
    est = json.loads(Path(a.state).read_text(encoding="utf-8"))
    st, resp = cli.llamar("GET", "/api/mailbox/in", "get_in", timeout=p["http_timeout_s"])
    lista = como_lista(parsear(resp))
    encontrados = buscar_mensajes(lista, est["run_uuid"])
    res = {"paso": "check-in", "run_uuid": est["run_uuid"], "codigo_http": st,
           "mensajes_en_entrada": len(lista), "mensajes_con_uuid": len(encontrados),
           "adjuntos": [], "resultado": "fallo"}
    if not encontrados:
        res["motivo"] = "el mensaje con ese UUID no aparece en la bandeja de entrada"
        return res
    msg = encontrados[0]
    mid = getk(msg, "MID", "Mid", "id")
    archivos = getk(msg, "Files", "Attachments") or []
    if mid is None:
        res["motivo"] = "no se encontró el campo MID en el JSON; revisar http_*_get_in.body"
        return res
    for f in archivos:
        nombre = getk(f, "Name", "Filename") if isinstance(f, dict) else str(f)
        st_d, contenido = cli.llamar("GET", f"/api/mailbox/in/{quote(str(mid))}/{quote(str(nombre))}",
                                     "get_adjunto", timeout=p["http_timeout_s"])
        sha = sha256_bytes(contenido) if st_d == 200 else None
        if st_d == 200:
            (rlog.dir / f"recibido_{nombre}").write_bytes(contenido)
        # Si la descarga falla, el cuerpo es un mensaje de error: no cuenta como contenido.
        res["adjuntos"].append({"nombre": nombre, "codigo_http": st_d,
                                "bytes": len(contenido) if st_d == 200 else 0,
                                "error": None if st_d == 200 else contenido.decode("utf-8", "replace")[:300],
                                "sha256": sha,
                                "coincide_con_envio": sha == est["adjunto_sha256"]})
    ok = any(x["coincide_con_envio"] for x in res["adjuntos"])
    res["sha256_recibido"] = next((x["sha256"] for x in res["adjuntos"]
                                   if x["coincide_con_envio"]), None)
    res["resultado"] = "aprobado" if ok else "fallo"
    if not ok:
        res["motivo"] = "ningún adjunto recibido coincide con el SHA-256 del envío"
    return res


def cmd_ack_queue(a, cfg, rlog, cli):
    p = cfg["pat"]
    cuerpo, tipo = multipart(
        {"to": a.to, "subject": f"ACK run={a.uuid}",
         "body": f"run={a.uuid}\nsha256={a.sha256}"}, [])
    st, _ = cli.llamar("POST", "/api/mailbox/out", "post_out_ack", datos=cuerpo, tipo=tipo,
                       timeout=p["http_timeout_s"])
    st2, resp2 = cli.llamar("GET", "/api/mailbox/out", "get_out", timeout=p["http_timeout_s"])
    en_salida = st2 is not None and f"ACK run={a.uuid}" in resp2.decode("utf-8", "replace")
    ok = st is not None and 200 <= st < 300 and en_salida
    return {"paso": "ack-queue", "codigo_post_out": st, "en_bandeja_salida": en_salida,
            "resultado": "ok" if ok else "fallo"}


def cmd_ack_verify(a, cfg, rlog, cli):
    p = cfg["pat"]
    est = json.loads(Path(a.state).read_text(encoding="utf-8"))
    st, resp = cli.llamar("GET", "/api/mailbox/in", "get_in", timeout=p["http_timeout_s"])
    lista = como_lista(parsear(resp))
    acks = buscar_mensajes(lista, f"ACK run={est['run_uuid']}")
    res = {"paso": "ack-verify", "run_uuid": est["run_uuid"], "codigo_http": st,
           "acks_encontrados": len(acks), "resultado": "fallo"}
    if not acks:
        res["motivo"] = "el ACK no llegó a la bandeja de entrada de A"
        return res
    texto = json.dumps(acks[0], ensure_ascii=False)
    res["uuid_en_mensaje"] = est["run_uuid"] in texto
    res["sha256_completo_en_mensaje"] = est["adjunto_sha256"] in texto
    if res["sha256_completo_en_mensaje"] and res["uuid_en_mensaje"]:
        res["resultado"] = "aprobado"
    else:
        res["resultado"] = "no_determinado"
        res["motivo"] = ("el cuerpo no aparece en el listado o no coincide; "
                         "revisar manualmente http_*_get_in.body")
    return res


def cmd_disconnect(a, cfg, rlog, cli):
    st, resp = cli.llamar("POST", "/api/disconnect", "post_disconnect", datos=b"",
                          timeout=cfg["pat"]["http_timeout_s"])
    return {"paso": "disconnect", "codigo_http": st,
            "cuerpo": resp.decode("utf-8", "replace")[:2000]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test de Pat por API HTTP.")
    ap.add_argument("subcomando", choices=["queue", "connect", "check-in", "ack-queue",
                                           "ack-verify", "disconnect"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--phase", choices=["e0", "smoke"], default="e0")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--size", type=int, default=200, help="queue: tamaño del adjunto en bytes")
    ap.add_argument("--call", help="connect: indicativo destino")
    ap.add_argument("--state", help="check-in / ack-verify: state.json del envío")
    ap.add_argument("--to", help="ack-queue: indicativo de A")
    ap.add_argument("--uuid", help="ack-queue: UUID de la corrida")
    ap.add_argument("--sha256", help="ack-queue: SHA-256 completo del adjunto recibido")
    a = ap.parse_args()

    faltan = {"connect": ["call"], "check-in": ["state"], "ack-verify": ["state"],
              "ack-queue": ["to", "uuid", "sha256"]}.get(a.subcomando, [])
    for f in faltan:
        if getattr(a, f) is None:
            ap.error(f"{a.subcomando} requiere --{f}")

    cfg = load_config(a.config)
    rlog = RunLog(cfg, a.phase, f"{a.tag}_{a.subcomando}")
    cli = Cliente(cfg, rlog)
    res: dict = {"paso": a.subcomando, "tag": a.tag, "run_id": rlog.run_id}
    try:
        capture_environment(cfg, rlog)
        funcion = {"queue": cmd_queue, "connect": cmd_connect, "check-in": cmd_checkin,
                   "ack-queue": cmd_ack_queue, "ack-verify": cmd_ack_verify,
                   "disconnect": cmd_disconnect}[a.subcomando]
        res.update(funcion(a, cfg, rlog, cli))
    except Exception as e:  # noqa: BLE001
        res["resultado"] = "fallo"
        res["excepcion"] = traceback.format_exc()
        rlog.log("excepcion", detalle=res["excepcion"])
    finally:
        ruta = rlog.save_json("result.json", res)
        rlog.log("fin", resultado=res.get("resultado"), json=str(ruta))
        rlog.close()
    print(f"\nResultado: {res.get('resultado', res.get('sesion'))}  ->  {ruta}")
    return 0 if res.get("resultado") in ("ok", "aprobado") or res.get("sesion") == "ok_http" \
        or a.subcomando == "disconnect" else 1


if __name__ == "__main__":
    sys.exit(main())
