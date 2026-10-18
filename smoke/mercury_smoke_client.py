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
  - Envía MYCALL, BW (según --bw-when), LISTEN ON / CONNECT, y juzga la respuesta a cada
    comando según docs/TNC.md de Mercury (rama mercuryv2): `OK` = aceptado, `WRONG` =
    rechazado, cualquier otra cosa o silencio = respuesta no reconocida (fallo). Guarda
    siempre las líneas crudas.
  - Registra la línea CONNECTED completa y la valida: `CONNECTED <origen> <destino> <ancho>`
    (origen = quien inició la sesión). Se exigen exactamente 4 campos; se compara el ancho
    (500 o BW500 se toleran) y los indicativos. Forma distinta, ancho o indicativos
    incorrectos son fallo. El SSID, si Mercury lo devuelve, se compara como parte del texto.
  - DISCONNECT con la sesión activa se juzga como cualquier comando. En la limpieza final
    (si no se vio DISCONNECTED) una respuesta que no sea OK es un estado permitido: se
    registra en `desconexion_limpieza` y lo que se juzga es el estado final.
  - En `listen`, `--bw-when after` envía BW tras CONNECTED, igual que el emisor.
  - Emisor: escribe en 8301 la cabecera de 16 B + payload, espera una línea `BUFFER 0`
    con marca de tiempo ESTRICTAMENTE posterior al inicio de la escritura, y manda
    DISCONNECT.
  - Receptor: lee 8301 hasta completar exactamente la longitud declarada, calcula
    el SHA-256 y lo compara con el truncado de la cabecera y con el payload
    esperado (misma semilla). Un fragmento corto no se trata como payload
    incompleto; 60 s sin bytes nuevos tras CONNECTED sí es timeout.
  - Cuenta los bytes recibidos en 8301: cualquier byte más allá de 16+longitud, o
    fuera del intervalo CONNECTED..DISCONNECTED, es un fallo.
  - Guarda bytes crudos de 8300 y 8301 (rx y tx, con índice de tiempos de cada
    envío y recepción), eventos con doble marca de tiempo, entorno inicial y final,
    configuración con hash, el estado de cada socket y un result.json por corrida.
  - Deja siempre dos campos separados: `arq_confirmado` (BUFFER 0, solo emisor) y
    `entregado_integro` (hash verificado en B, solo receptor).

QUÉ NO HACE
  - No encadena variantes ni repeticiones: una ejecución = una sesión nueva.
  - No reintenta ni reinicia Mercury; no modifica Mercury ni su configuración.
  - No decide que algo "llegó" por BUFFER 0: el emisor deja `entregado_integro`
    en null. La entrega la establece smoke_cross_check.py con el resultado de B.
  - Supone que Mercury responde OK/WRONG una vez por comando y EN ORDEN (precondición a
    verificar en E0-2). Si hubiera respuestas atrasadas, una OK podría asociarse al comando
    siguiente; el script no correlaciona respuestas por comando.
  - No distingue, solo por marcas de recepción, un BUFFER 0 viejo retenido en el búfer TCP.
    Anota `buffer_positivo_antes_de_cero` (observación) para decidir en v7 si se exige.
  - El código de salida 0 de `send` significa SOLO "sesión emisora completada": no es
    entrega. La entrega se lee en el resultado del receptor y en el cruce.
  - No espera a que B termine de validar antes de enviar DISCONNECT (v6, H1, paso 4).
    Si B no recibe todo por ese motivo, aparecerá como fallo en B; es algo a observar en E0-1.
  - No usa simulador de canal ni mide rendimiento (es un smoke test).

POR QUÉ
  - Una sesión por ejecución cumple la regla de E0-2 de no encadenar variantes y de
    partir desconectado en cada repetición de H1.
  - BUFFER 0 es confirmación a nivel ARQ (docs/TNC.md), no entrega de aplicación;
    por eso la entrega solo la prueba el hash en el receptor.
  - Una respuesta WRONG a MYCALL, LISTEN ON o CONNECT invalida la prueba: sin eso podría
    aprobarse una sesión montada sobre una configuración rechazada.
  - BW es especial: en E0-2 la variante V2 (BW tras CONNECT) puede ser rechazada, y eso es
    un resultado a clasificar (`bw_aceptado`), no un error del script. Solo es fallo si BW se
    envía ANTES de conectar y se rechaza, o si el ancho de CONNECTED no coincide.
  - Los eventos de 8300 y los datos de 8301 llegan por sockets distintos; se
    admite una tolerancia (session_tolerance_ms) al comparar sus tiempos.
  - `BUFFER 0` puede aparecer también antes de enviar datos; por eso solo se acepta una
    línea posterior al inicio de la escritura y se registran todas las líneas BUFFER.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback

from smoke_common import (HEADER_LEN, RunLog, TcpCapture, capture_environment,
                          capture_final, load_config, problemas_de_evidencia, make_header, make_payload,
                          mono_ns, parse_header, sha256_bytes)

RX_CONNECTED = r"^\s*CONNECTED\b"
RX_DISCONNECTED = r"^\s*DISCONNECTED\b"
RX_BUFFER = r"^\s*BUFFER\s+(\d+)"
RX_RESP = r"^\s*(OK|WRONG)\b"


def segundos(a: int | None, b: int | None):
    return None if a is None or b is None else (b - a) / 1e9


def solo_digitos(t: str) -> str:
    return re.sub(r"\D", "", t)


def hay_error(lineas, patrones) -> bool:
    return any(re.search(p, l, re.I) for p in patrones for l in lineas)


def enviar_comando(cmd: TcpCapture, texto: str, ventana_s: float) -> dict:
    """Envía un comando y espera su respuesta OK/WRONG (docs/TNC.md). Devuelve las líneas
    crudas recibidas y el veredicto: "OK", "WRONG" o None si no hubo respuesta reconocida."""
    idx = cmd.nlines()
    cmd.send(texto.encode("ascii") + b"\r", etiqueta=texto)
    r = cmd.wait_line(RX_RESP, ventana_s, start=idx)
    time.sleep(0.1)  # recoge eventos que lleguen pegados a la respuesta
    with cmd.cond:
        lineas = [l[1] for l in cmd.lines[idx:]]
    veredicto = re.match(RX_RESP, r[1][1]).group(1) if r else None
    cmd.rlog.log("respuesta_comando", comando=texto, veredicto=veredicto, lineas=lineas)
    return {"comando": texto, "respuesta": veredicto, "lineas": lineas}


def juzgar(c: dict, etiqueta: str, fallos: list[str]) -> bool:
    """Registra fallo si la respuesta no fue OK. Devuelve True si el comando fue aceptado."""
    if c["respuesta"] == "OK":
        return True
    if c["respuesta"] == "WRONG":
        fallos.append(f"{etiqueta}: Mercury respondió WRONG ({c['comando']})")
    else:
        fallos.append(f"{etiqueta}: sin respuesta OK/WRONG reconocida ({c['comando']}); "
                      f"líneas: {c['lineas']}")
    return False


def analizar_connected(texto: str, bw_cmd: str, mi: str, otro: str, rol: str) -> dict:
    """CONNECTED <origen> <destino> <ancho> (docs/TNC.md). El origen es quien inició la
    sesión: el emisor (mi) en `send` y el otro PC en `listen`."""
    campos = texto.split()
    esperado = solo_digitos(bw_cmd)
    info = {"linea": texto, "campos": campos, "ancho_esperado": esperado,
            "forma_valida": len(campos) == 4,  # CONNECTED origen destino ancho
            "ancho_coincide": None, "indicativos_coinciden": None}
    if len(campos) == 4:
        info["ancho_coincide"] = solo_digitos(campos[-1]) == esperado
        esp = (mi, otro) if rol == "send" else (otro, mi)
        info["indicativos_coinciden"] = (campos[1].upper(), campos[2].upper()) == \
            (esp[0].upper(), esp[1].upper())
        info["indicativos_esperados"] = list(esp)
    return info


def fallos_de_connected(info: dict) -> list[str]:
    f = []
    if not info["forma_valida"]:
        f.append("línea CONNECTED con forma inesperada: se esperan 4 campos "
                 "(CONNECTED origen destino ancho)")
        return f
    if info["ancho_coincide"] is not True:
        f.append("ancho reportado en CONNECTED no coincide con el configurado")
    if info["indicativos_coinciden"] is not True:
        f.append("los indicativos de CONNECTED no son los esperados")
    return f


def asegurar_desconexion(cmd: TcpCapture, espera_s: float, desde: int, win: float):
    """Si no se vio DISCONNECTED, envía DISCONNECT de limpieza y confirma el estado final.
    Aquí una respuesta que no sea OK es un estado PERMITIDO (la sesión pudo terminar justo
    antes): no es fallo, pero se devuelve para registrarla en `desconexion_limpieza`.
    Lo que se juzga es el estado final (DISCONNECTED visto o no)."""
    visto = cmd.wait_line(RX_DISCONNECTED, 0.1, start=desde)
    limpieza = None
    if visto is None:
        limpieza = enviar_comando(cmd, "DISCONNECT", win)
        visto = cmd.wait_line(RX_DISCONNECTED, espera_s, start=desde)
    return ("desconectado" if visto else "no_confirmado"), limpieza


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
    respuestas: dict[str, dict] = {}

    def enviar_bw():
        respuestas["bw"] = enviar_comando(cmd, m["bw_command"], win)
        res["bw_aceptado"] = respuestas["bw"]["respuesta"] == "OK"
        if args.bw_when == "before" and not res["bw_aceptado"]:
            juzgar(respuestas["bw"], "BW antes de CONNECT", fallos)

    respuestas["mycall"] = enviar_comando(cmd, f"MYCALL {m['mycall']}", win)
    juzgar(respuestas["mycall"], "MYCALL", fallos)
    if args.bw_when == "before":
        enviar_bw()

    idx0 = cmd.nlines()
    t_cmd = mono_ns()
    t_conn = None
    if not fallos:
        respuestas["connect"] = enviar_comando(cmd, f"CONNECT {m['mycall']} {m['peer_call']}", win)
        if juzgar(respuestas["connect"], "CONNECT", fallos):
            r = cmd.wait_line(RX_CONNECTED, m["connect_timeout_s"], start=idx0)
            if r is None:
                fallos.append("no hubo CONNECTED")
            else:
                t_conn = r[1][0]
                res["connected"] = analizar_connected(r[1][1], m["bw_command"], m["mycall"],
                                                      m["peer_call"], "send")
                fallos += fallos_de_connected(res["connected"])
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
                b0 = cmd.wait_line_after(r"^\s*BUFFER\s+0\b", m["transfer_timeout_s"],
                                         start=idx_w, t_min_ns=t_w0)
                with cmd.cond:
                    buffers = [(segundos(t_w0, t), txt) for t, txt in cmd.lines[idx_w:]
                               if re.search(RX_BUFFER, txt)]
                res["lineas_buffer"] = buffers
                t_cero = segundos(t_w0, b0[1][0]) if b0 else None
                # Observación (no criterio): si Mercury informa BUFFER > 0 tras escribir, el
                # BUFFER 0 posterior es una transición real y no un valor viejo retrasado.
                res["buffer_positivo_antes_de_cero"] = any(
                    int(re.search(RX_BUFFER, txt).group(1)) > 0 and (t_cero is None or t <= t_cero)
                    for t, txt in buffers)
                if b0 is None:
                    fallos.append("no se vio BUFFER 0 posterior al envío (timeout o cierre)")
                else:
                    res["arq_confirmado"] = True
                    res["t_transferencia_s"] = segundos(t_w0, b0[1][0])
                # La sesión sigue activa: DISCONNECT debe ser aceptado (OK).
                respuestas["disconnect"] = enviar_comando(cmd, "DISCONNECT", win)
                juzgar(respuestas["disconnect"], "DISCONNECT", fallos)
    res["t_conexion_s"] = segundos(t_cmd, t_conn)
    res["estado_final"], res["desconexion_limpieza"] = asegurar_desconexion(
        cmd, m["disconnect_wait_s"], idx0, win)
    if res["estado_final"] != "desconectado":
        fallos.append("no se confirmó DISCONNECTED")
    res["respuestas"] = respuestas
    res["error_en_respuestas"] = hay_error(
        [l for v in respuestas.values() for l in v["lineas"]], m.get("error_patterns", []))
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
    respuestas: dict[str, dict] = {}

    def enviar_bw():
        respuestas["bw"] = enviar_comando(cmd, m["bw_command"], win)
        res["bw_aceptado"] = respuestas["bw"]["respuesta"] == "OK"
        if args.bw_when == "before" and not res["bw_aceptado"]:
            juzgar(respuestas["bw"], "BW antes de LISTEN ON", fallos)

    respuestas["mycall"] = enviar_comando(cmd, f"MYCALL {m['mycall']}", win)
    juzgar(respuestas["mycall"], "MYCALL", fallos)
    if args.bw_when == "before":
        enviar_bw()
    idx0 = cmd.nlines()
    t_conn = t_disc = None
    if not fallos:
        respuestas["listen"] = enviar_comando(cmd, "LISTEN ON", win)
        if juzgar(respuestas["listen"], "LISTEN ON", fallos):
            rlog.log("esperando_conexion")
            r = cmd.wait_line(RX_CONNECTED, m["listen_timeout_s"], start=idx0)
            if r is None:
                fallos.append("no hubo CONNECTED")
            else:
                t_conn = r[1][0]
                res["connected"] = analizar_connected(r[1][1], m["bw_command"], m["mycall"],
                                                      m["peer_call"], "listen")
                fallos += fallos_de_connected(res["connected"])
                if args.bw_when == "after":   # igual que el emisor: tras CONNECTED
                    enviar_bw()
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
    res["estado_final"], res["desconexion_limpieza"] = asegurar_desconexion(
        cmd, m["disconnect_wait_s"], idx0, win)

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
        [l for v in respuestas.values() for l in v["lineas"]], m.get("error_patterns", []))
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
    puertos = (m["cmd_port"], m["data_port"])
    cmd = data = None
    env: dict = {}
    res: dict = {"modo": args.modo, "tag": args.tag, "run_id": rlog.run_id}
    codigo = 1
    try:
        env = capture_environment(cfg, rlog, puertos=puertos)
        cmd = TcpCapture("8300", m["host"], m["cmd_port"], rlog, line_mode=True)
        data = TcpCapture("8301", m["host"], m["data_port"], rlog, line_mode=False)
        res = (run_send if args.modo == "send" else run_listen)(args, cfg, rlog, cmd, data)
        codigo = 0 if (res.get("completado_emisor") or res.get("entregado_integro")) else 1
    except Exception as e:  # noqa: BLE001 - se registra el error completo
        res["excepcion"] = traceback.format_exc()
        res["fallos"] = res.get("fallos", []) + [f"excepción: {e!r}"]
        rlog.log("excepcion", detalle=res["excepcion"])
    finally:
        # Estado de cada socket: distingue cierre limpio, timeout y error del socket.
        res["sockets"] = {s.nombre: s.estado() for s in (cmd, data) if s}
        for s in (cmd, data):
            if s:
                s.close()
        fin = capture_final(cfg, rlog, puertos=puertos)
        res.update(problemas_de_evidencia(cfg, env, fin))
        if res["evidencia_incompleta"]:
            codigo = codigo or 1
        ruta = rlog.save_json("result.json", res)
        rlog.log("fin", resultado_json=str(ruta), fallos=res.get("fallos"))
        rlog.close()
    print(f"\nResultado guardado en: {ruta}")
    if res.get("evidencia_incompleta"):
        print("EVIDENCIA INCOMPLETA:", res["evidencia_incompleta"])
    if args.modo == "send":
        print("Código 0 = sesión emisora completada. NO prueba entrega: falta el resultado de B "
              "(smoke_cross_check.py).")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
