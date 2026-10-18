"""mercury_smoke_client.py - cliente de prueba para Mercury 1.9.19 (puertos 8300/8301).

Cubre E0-1 (payload por 8301), E0-2 (orden de BW y negociación) y H1.
Se ejecuta UNA vez por sesión: `listen` en el PC B y `send` en el PC A.

USO
  PC B:  python mercury_smoke_client.py listen --tag E0-1_1KB --payload-size 1024
  PC A:  python mercury_smoke_client.py send   --tag E0-1_1KB --payload-size 1024
  Variantes de E0-2:  --bw-when before | after | none   (en ambos PCs)
  Fase formal:        --phase smoke --tag H1-1
  Después:            python smoke_cross_check.py <result emisor> <result receptor>

QUÉ HACE
  - Envía MYCALL, BW (según --bw-when), LISTEN ON / CONNECT, y registra la respuesta
    a cada comando y la línea CONNECTED completa, tal como aparece.
  - Emisor: escribe en 8301 la cabecera de 16 B + payload, espera una línea
    `BUFFER 0` posterior al envío y manda DISCONNECT.
  - Receptor: lee 8301 hasta completar exactamente la longitud declarada, calcula
    el SHA-256 y lo compara con el truncado de la cabecera y con el payload
    esperado (misma semilla). Un fragmento corto no se trata como payload
    incompleto; 60 s sin bytes nuevos tras CONNECTED sí es timeout.
  - Cuenta los bytes recibidos en 8301: cualquier byte más allá de 16+longitud, o
    fuera del intervalo CONNECTED..DISCONNECTED, es un fallo.
  - Guarda bytes crudos de 8300 y 8301 (rx y tx), eventos con doble marca de tiempo,
    entorno, configuración con hash y un result.json por corrida.
  - Deja siempre dos campos separados: `arq_confirmado` (BUFFER 0, solo emisor) y
    `entregado_integro` (hash verificado en B, solo receptor).

QUÉ NO HACE
  - No encadena variantes ni repeticiones: una ejecución = una sesión nueva.
  - No reintenta ni reinicia Mercury; no modifica Mercury ni su configuración.
  - No decide que algo "llegó" por BUFFER 0: el emisor deja `entregado_integro`
    en null. La entrega la establece smoke_cross_check.py con el resultado de B.
  - No interpreta respuestas de Mercury como OK o error salvo que se configuren
    `error_patterns`; las guarda crudas para leerlas en E0-2.
  - No usa simulador de canal ni mide rendimiento (es un smoke test).

POR QUÉ
  - Una sesión por ejecución cumple la regla de E0-2 de no encadenar variantes y de
    partir desconectado en cada repetición de H1.
  - BUFFER 0 es confirmación a nivel ARQ (docs/TNC.md), no entrega de aplicación;
    por eso la entrega solo la prueba el hash en el receptor.
  - Los eventos de 8300 y los datos de 8301 llegan por sockets distintos; se
    admite una tolerancia (session_tolerance_ms) al comparar sus tiempos.
  - `BUFFER 0` puede aparecer también antes de enviar datos; por eso solo se
    acepta una línea posterior al inicio de la escritura y se registran todas
    las líneas BUFFER para revisión manual.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback

from smoke_common import (HEADER_LEN, RunLog, TcpCapture, capture_environment,
                          load_config, make_header, make_payload, mono_ns,
                          parse_header, sha256_bytes)

RX_CONNECTED = r"^\s*CONNECTED\b"
RX_DISCONNECTED = r"^\s*DISCONNECTED\b"
RX_BUFFER = r"^\s*BUFFER\s+(\d+)"


def segundos(a: int | None, b: int | None):
    return None if a is None or b is None else (b - a) / 1e9


def hay_error(lineas, patrones) -> bool:
    return any(re.search(p, l, re.I) for p in patrones for l in lineas)


def analizar_connected(texto: str, bw_cmd: str) -> dict:
    campos = texto.split()
    esperado = re.sub(r"\D", "", bw_cmd)
    # El formato del último campo (500 o BW500) está pendiente de confirmar:
    # se guarda tal cual y se compara solo por sus dígitos.
    coincide = None
    if len(campos) >= 4:
        coincide = re.sub(r"\D", "", campos[-1]) == esperado
    return {"linea": texto, "campos": campos, "ancho_esperado": esperado,
            "ancho_coincide": coincide}


def asegurar_desconexion(cmd: TcpCapture, espera_s: float, desde: int) -> str:
    """Envía DISCONNECT si no se vio DISCONNECTED y confirma el estado final."""
    visto = cmd.wait_line(RX_DISCONNECTED, 0.1, start=desde)
    if visto is None:
        cmd.command("DISCONNECT", 0.2)
        visto = cmd.wait_line(RX_DISCONNECTED, espera_s, start=desde)
    return "desconectado" if visto else "no_confirmado"


def run_send(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = cfg["mercury"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    payload = make_payload(args.payload_size, seed)
    header = make_header(payload)
    res = {"modo": "send", "tag": args.tag, "run_id": rlog.run_id,
           "mycall": m["mycall"], "destino": m["peer_call"], "bw_comando": m["bw_command"],
           "bw_cuando": args.bw_when, "payload_bytes": len(payload),
           "payload_sha256": sha256_bytes(payload), "cabecera_hex": header.hex(),
           "arq_confirmado": False, "entregado_integro": None}
    fallos: list[str] = []
    respuestas: dict[str, list[str]] = {}

    def enviar_bw():
        respuestas["bw"] = cmd.command(m["bw_command"], win)

    respuestas["mycall"] = cmd.command(f"MYCALL {m['mycall']}", win)
    if args.bw_when == "before":
        enviar_bw()

    idx0 = cmd.nlines()
    t_cmd = mono_ns()
    respuestas["connect"] = cmd.command(f"CONNECT {m['mycall']} {m['peer_call']}", 0.2)
    r = cmd.wait_line(RX_CONNECTED, m["connect_timeout_s"], start=idx0)
    t_conn = None
    if r is None:
        fallos.append("no hubo CONNECTED")
    else:
        t_conn = r[1][0]
        res["connected"] = analizar_connected(r[1][1], m["bw_command"])
        if res["connected"]["ancho_coincide"] is False:
            fallos.append("ancho reportado en CONNECTED no coincide con el configurado")
        if args.bw_when == "after":
            enviar_bw()

        idx_w = cmd.nlines()
        t_w0 = mono_ns()
        try:
            data.send(header + payload, etiqueta="cabecera+payload")
        except OSError as e:
            fallos.append(f"error al escribir en 8301: {e!r}")
        t_w1 = mono_ns()
        res["t_escritura_s"] = segundos(t_w0, t_w1)
        b0 = cmd.wait_line(r"^\s*BUFFER\s+0\b", m["transfer_timeout_s"], start=idx_w)
        with cmd.cond:
            buffers = [(segundos(t_w0, t), txt) for t, txt in cmd.lines[idx_w:]
                       if re.search(RX_BUFFER, txt)]
        res["lineas_buffer"] = buffers
        if b0 is None:
            fallos.append("no se vio BUFFER 0 tras el envío (timeout o cierre)")
        else:
            res["arq_confirmado"] = True
            res["t_transferencia_s"] = segundos(t_w0, b0[1][0])
        respuestas["disconnect"] = cmd.command("DISCONNECT", 0.2)
    res["t_conexion_s"] = segundos(t_cmd, t_conn)
    res["estado_final"] = asegurar_desconexion(cmd, m["disconnect_wait_s"], idx0)
    if res["estado_final"] != "desconectado":
        fallos.append("no se confirmó DISCONNECTED")
    res["respuestas"] = respuestas
    res["error_en_respuestas"] = hay_error(
        [l for v in respuestas.values() for l in v], m.get("error_patterns", []))
    if res["error_en_respuestas"]:
        fallos.append("una respuesta coincide con error_patterns")
    res["fallos"] = fallos
    res["completado_emisor"] = not fallos
    return res


def run_listen(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = cfg["mercury"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    res = {"modo": "listen", "tag": args.tag, "run_id": rlog.run_id,
           "mycall": m["mycall"], "bw_comando": m["bw_command"], "bw_cuando": args.bw_when,
           "entregado_integro": False, "arq_confirmado": None}
    fallos: list[str] = []
    respuestas: dict[str, list[str]] = {}

    respuestas["mycall"] = cmd.command(f"MYCALL {m['mycall']}", win)
    if args.bw_when == "before":
        respuestas["bw"] = cmd.command(m["bw_command"], win)
    idx0 = cmd.nlines()
    respuestas["listen"] = cmd.command("LISTEN ON", win)
    if args.bw_when == "after":
        respuestas["bw"] = cmd.command(m["bw_command"], win)

    rlog.log("esperando_conexion")
    r = cmd.wait_line(RX_CONNECTED, m["listen_timeout_s"], start=idx0)
    t_conn = t_disc = None
    if r is None:
        fallos.append("no hubo CONNECTED")
    else:
        t_conn = r[1][0]
        res["connected"] = analizar_connected(r[1][1], m["bw_command"])
        if res["connected"]["ancho_coincide"] is False:
            fallos.append("ancho reportado en CONNECTED no coincide con el configurado")
        try:
            cab = data.read_exact(HEADER_LEN, m["idle_timeout_s"])
            largo, hash_trunc = parse_header(cab)
            res["longitud_declarada"] = largo
            if largo > cfg["general"]["max_payload_bytes"]:
                raise ValueError(f"longitud declarada absurda: {largo}")
            cuerpo = data.read_exact(largo, m["idle_timeout_s"])
            t_fin = mono_ns()
            res["t_transferencia_s"] = segundos(t_conn, t_fin)
            sha = sha256_bytes(cuerpo)
            res["payload_sha256"] = sha
            res["hash_truncado_coincide"] = bytes.fromhex(sha)[:8] == hash_trunc
            esperado = sha256_bytes(make_payload(largo, seed))
            res["coincide_con_payload_esperado"] = (sha == esperado)
            if not res["hash_truncado_coincide"]:
                fallos.append("el SHA-256 no coincide con la cabecera")
            if not res["coincide_con_payload_esperado"]:
                fallos.append("el payload no coincide con el generado con la semilla")
        except (TimeoutError, ConnectionError, ValueError) as e:
            fallos.append(f"lectura de 8301 fallida: {e!r}")

        # Bytes extra: espera de gracia y luego cuenta todo lo recibido en 8301.
        time.sleep(m["extra_bytes_grace_s"])
        d = cmd.wait_line(RX_DISCONNECTED, m["disconnect_wait_s"], start=idx0)
        if d:
            t_disc = d[1][0]
    res["estado_final"] = asegurar_desconexion(cmd, m["disconnect_wait_s"], idx0)

    esperado_total = HEADER_LEN + res.get("longitud_declarada", 0)
    total = sum(n for _, n in data.chunks)
    res["bytes_8301_totales"] = total
    res["bytes_8301_esperados"] = esperado_total
    if total > esperado_total:
        fallos.append(f"bytes extra en 8301: {total - esperado_total}")
    tol = m["session_tolerance_ms"] * 1_000_000
    fuera = sum(n for t, n in data.chunks
                if (t_conn is None or t < t_conn - tol)
                or (t_disc is not None and t > t_disc + tol))
    res["bytes_8301_fuera_de_sesion"] = fuera
    if fuera:
        fallos.append(f"{fuera} bytes en 8301 fuera de la sesión CONNECTED")
    res["bloques_8301"] = [{"t_mono_ns": t, "bytes": n} for t, n in data.chunks]

    if res["estado_final"] != "desconectado":
        fallos.append("no se confirmó DISCONNECTED")
    res["respuestas"] = respuestas
    res["error_en_respuestas"] = hay_error(
        [l for v in respuestas.values() for l in v], m.get("error_patterns", []))
    if res["error_en_respuestas"]:
        fallos.append("una respuesta coincide con error_patterns")
    res["fallos"] = fallos
    res["entregado_integro"] = (not fallos and res.get("coincide_con_payload_esperado") is True
                                and res.get("hash_truncado_coincide") is True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Cliente de smoke test para Mercury.")
    ap.add_argument("modo", choices=["send", "listen"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--phase", choices=["e0", "smoke"], default="e0")
    ap.add_argument("--tag", required=True, help="Ej.: E0-1_1KB, E0-2_V1, H1-1")
    ap.add_argument("--payload-size", type=int, default=200)
    ap.add_argument("--bw-when", choices=["before", "after", "none"], default="before",
                    help="before: BW antes de CONNECT/LISTEN ON; after: tras CONNECTED "
                         "(emisor) o tras LISTEN ON (receptor); none: no enviar BW")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rlog = RunLog(cfg, args.phase, args.tag)
    m = cfg["mercury"]
    cmd = data = None
    res: dict = {"modo": args.modo, "tag": args.tag, "run_id": rlog.run_id}
    codigo = 1
    try:
        capture_environment(cfg, rlog, puertos=(m["cmd_port"], m["data_port"]))
        cmd = TcpCapture("8300", m["host"], m["cmd_port"], rlog, line_mode=True)
        data = TcpCapture("8301", m["host"], m["data_port"], rlog, line_mode=False)
        res = (run_send if args.modo == "send" else run_listen)(args, cfg, rlog, cmd, data)
        codigo = 0 if (res.get("completado_emisor") or res.get("entregado_integro")) else 1
    except Exception as e:  # noqa: BLE001 - se registra el error completo
        res["excepcion"] = traceback.format_exc()
        res["fallos"] = res.get("fallos", []) + [f"excepción: {e!r}"]
        rlog.log("excepcion", detalle=res["excepcion"])
    finally:
        for s in (cmd, data):
            if s:
                s.close()
        ruta = rlog.save_json("result.json", res)
        rlog.log("fin", resultado_json=str(ruta), fallos=res.get("fallos"))
        rlog.close()
    print(f"\nResultado guardado en: {ruta}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
