"""Mercury 1.9.19 client for Modem Smoke Profile MS-1.0.

One invocation performs one fresh sender or receiver session for E0-1, E0-2,
or H1. The sender reports native ARQ confirmation separately from verified
delivery. The receiver validates frame length and SHA-256. Use
smoke_cross_check.py to establish end-to-end delivery.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback

from smoke_common import (HEADER_LEN, RunLog, TcpCapture, capture_environment,
                          capture_final, load_config, evidence_issues, make_header, make_payload,
                          mono_ns, parse_header, sha256_bytes)

RX_CONNECTED = r"^\s*CONNECTED\b"
RX_DISCONNECTED = r"^\s*DISCONNECTED\b"
RX_BUFFER = r"^\s*BUFFER\s+(\d+)"
RX_RESP = r"^\s*(OK|WRONG)\b"


def seconds_between(a: int | None, b: int | None):
    return None if a is None or b is None else (b - a) / 1e9


def digits_only(t: str) -> str:
    return re.sub(r"\D", "", t)


def has_positive_buffer(lines_ns, t_write_ns: int, t_zero_ns: int | None) -> bool:
    """Return whether a positive BUFFER occurred after writing and before BUFFER 0."""
    for t, txt in lines_ns:
        m = re.search(RX_BUFFER, txt)
        if m and int(m.group(1)) > 0 and t > t_write_ns and (t_zero_ns is None or t <= t_zero_ns):
            return True
    return False


def has_error(lines, patterns) -> bool:
    return any(re.search(pattern, line, re.I) for pattern in patterns for line in lines)


def send_command(cmd: TcpCapture, text: str, window_s: float) -> dict:
    """Send one command and return its recognized OK or WRONG response."""
    idx = cmd.nlines()
    cmd.send(text.encode("ascii") + b"\r", label=text)
    r = cmd.wait_line(RX_RESP, window_s, start=idx)
    time.sleep(0.1)
    with cmd.cond:
        lines = [line[1] for line in cmd.lines[idx:]]
    response = re.match(RX_RESP, r[1][1]).group(1) if r else None
    cmd.rlog.log("command_response", command=text, response=response, lines=lines)
    return {"command": text, "response": response, "lines": lines}


def judge_command(result: dict, label: str, failures: list[str]) -> bool:
    """Record a failure unless the command response is OK."""
    if result["response"] == "OK":
        return True
    if result["response"] == "WRONG":
        failures.append(f"{label}: Mercury returned WRONG ({result['command']})")
    else:
        failures.append(f"{label}: no recognized OK/WRONG response ({result['command']}); "
                        f"lines: {result['lines']}")
    return False


def parse_connected(text: str, bandwidth_command: str, mycall: str, peer_call: str,
                    role: str) -> dict:
    """Parse and validate a Mercury CONNECTED event."""
    fields = text.split()
    expected_bandwidth = digits_only(bandwidth_command)
    info = {"line": text, "fields": fields, "expected_bandwidth": expected_bandwidth,
            "valid_form": len(fields) == 4,
            "bandwidth_matches": None, "callsigns_match": None}
    if len(fields) == 4:
        info["bandwidth_matches"] = digits_only(fields[-1]) == expected_bandwidth
        expected_callsigns = (mycall, peer_call) if role == "send" else (peer_call, mycall)
        info["callsigns_match"] = (fields[1].upper(), fields[2].upper()) == \
            (expected_callsigns[0].upper(), expected_callsigns[1].upper())
        info["expected_callsigns"] = list(expected_callsigns)
    return info


def connected_failures(info: dict) -> list[str]:
    if not info["valid_form"]:
        return ["CONNECTED has an unexpected form; expected 4 fields "
                "(CONNECTED origin destination bandwidth)"]
    failures = []
    if info["bandwidth_matches"] is not True:
        failures.append("CONNECTED bandwidth does not match the configured bandwidth")
    if info["callsigns_match"] is not True:
        failures.append("CONNECTED callsigns do not match the expected callsigns")
    return failures


def ensure_disconnected(cmd: TcpCapture, espera_s: float, desde: int, win: float):
    """Confirm the final DISCONNECTED event, sending cleanup if required."""
    visto = cmd.wait_line(RX_DISCONNECTED, 0.1, start=desde)
    limpieza = None
    if visto is None:
        limpieza = send_command(cmd, "DISCONNECT", win)
        visto = cmd.wait_line(RX_DISCONNECTED, espera_s, start=desde)
    return ("disconnected" if visto else "not_confirmed"), limpieza


def run_send(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = cfg["mercury"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    payload = make_payload(args.payload_size, seed)
    header = make_header(payload)
    res = {"mode": "send", "tag": args.tag, "run_id": rlog.run_id,
           "protocol_version": cfg["general"].get("protocol_version", "5.0"),
           "smoke_profile": cfg["general"].get("smoke_profile", "MS-1.0"),
           "mycall": m["mycall"], "destination": m["peer_call"], "bw_command": m["bw_command"],
           "bw_when": args.bw_when, "payload_bytes": len(payload),
           "payload_sha256": sha256_bytes(payload), "header_hex": header.hex(),
           "arq_confirmed": False, "delivered_intact": None}
    failures: list[str] = []
    responses: dict[str, dict] = {}

    def enviar_bw():
        responses["bw"] = send_command(cmd, m["bw_command"], win)
        res["bw_accepted"] = responses["bw"]["response"] == "OK"
        if args.bw_when == "before" and not res["bw_accepted"]:
            judge_command(responses["bw"], "BW antes de CONNECT", failures)

    responses["mycall"] = send_command(cmd, f"MYCALL {m['mycall']}", win)
    judge_command(responses["mycall"], "MYCALL", failures)
    if args.bw_when == "before":
        enviar_bw()

    idx0 = cmd.nlines()
    t_cmd = mono_ns()
    t_conn = None
    if not failures:
        responses["connect"] = send_command(cmd, f"CONNECT {m['mycall']} {m['peer_call']}", win)
        if judge_command(responses["connect"], "CONNECT", failures):
            r = cmd.wait_line(RX_CONNECTED, m["connect_timeout_s"], start=idx0)
            if r is None:
                failures.append("no CONNECTED event was observed")
            else:
                t_conn = r[1][0]
                res["connected"] = parse_connected(r[1][1], m["bw_command"], m["mycall"],
                                                      m["peer_call"], "send")
                failures += connected_failures(res["connected"])
                if args.bw_when == "after":
                    enviar_bw()

                idx_w = cmd.nlines()
                t_w0 = mono_ns()
                try:
                    data.send(header + payload, label="cabecera+payload")
                except OSError as e:
                    failures.append(f"error while writing port 8301: {e!r}")
                t_w1 = mono_ns()
                res["write_s"] = seconds_between(t_w0, t_w1)
                b0 = cmd.wait_line_after(r"^\s*BUFFER\s+0\b", m["transfer_timeout_s"],
                                         start=idx_w, t_min_ns=t_w0)
                with cmd.cond:
                    buffers_ns = [(t, txt) for t, txt in cmd.lines[idx_w:]
                                  if re.search(RX_BUFFER, txt)]
                res["buffer_lines"] = [(seconds_between(t_w0, t), txt) for t, txt in buffers_ns]
                # Observation, not a pass criterion: a positive BUFFER preceding zero
                # shows a current buffer transition rather than a delayed stale value.
                res["positive_buffer_before_zero"] = has_positive_buffer(
                    buffers_ns, t_w0, b0[1][0] if b0 else None)
                if b0 is None:
                    failures.append("B UFFER 0 was not observed after writing (timeout o cierre)")
                else:
                    res["arq_confirmed"] = True
                    res["t_transferencia_s"] = seconds_between(t_w0, b0[1][0])
                # The session is active, so DISCONNECT must be accepted.
                responses["disconnect"] = send_command(cmd, "DISCONNECT", win)
                judge_command(responses["disconnect"], "DISCONNECT", failures)
    res["connection_s"] = seconds_between(t_cmd, t_conn)
    res["final_state"], res["cleanup_disconnect"] = ensure_disconnected(
        cmd, m["disconnect_wait_s"], idx0, win)
    if res["final_state"] != "disconnected":
        failures.append("DISCONNECTED was not confirmed")
    res["responses"] = responses
    res["command_response_error"] = has_error(
        [l for v in responses.values() for l in v["lines"]], m.get("error_patterns", []))
    if res["command_response_error"]:
        failures.append("a command response matches error_patterns")
    res["failures"] = failures
    res["sender_completed"] = not failures
    return res


def run_listen(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = cfg["mercury"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    res = {"mode": "listen", "tag": args.tag, "run_id": rlog.run_id,
           "protocol_version": cfg["general"].get("protocol_version", "5.0"),
           "smoke_profile": cfg["general"].get("smoke_profile", "MS-1.0"),
           "mycall": m["mycall"], "bw_command": m["bw_command"], "bw_when": args.bw_when,
           "delivered_intact": False, "arq_confirmed": None}
    failures: list[str] = []
    responses: dict[str, dict] = {}

    def enviar_bw():
        responses["bw"] = send_command(cmd, m["bw_command"], win)
        res["bw_accepted"] = responses["bw"]["response"] == "OK"
        if args.bw_when == "before" and not res["bw_accepted"]:
            judge_command(responses["bw"], "BW antes de LISTEN ON", failures)

    responses["mycall"] = send_command(cmd, f"MYCALL {m['mycall']}", win)
    judge_command(responses["mycall"], "MYCALL", failures)
    if args.bw_when == "before":
        enviar_bw()
    idx0 = cmd.nlines()
    t_conn = t_disc = None
    if not failures:
        responses["listen"] = send_command(cmd, "LISTEN ON", win)
        if judge_command(responses["listen"], "LISTEN ON", failures):
            rlog.log("waiting_for_connection")
            r = cmd.wait_line(RX_CONNECTED, m["listen_timeout_s"], start=idx0)
            if r is None:
                failures.append("no CONNECTED event was observed")
            else:
                t_conn = r[1][0]
                res["connected"] = parse_connected(r[1][1], m["bw_command"], m["mycall"],
                                                      m["peer_call"], "listen")
                failures += connected_failures(res["connected"])
                if args.bw_when == "after":   # igual que el emisor: tras CONNECTED
                    enviar_bw()
                try:
                    cab = data.read_exact(HEADER_LEN, m["idle_timeout_s"])
                    largo, hash_trunc = parse_header(cab)
                    res["declared_length"] = largo
                    if largo > cfg["general"]["max_payload_bytes"]:
                        raise ValueError(f"declared length is unreasonable: {largo}")
                    cuerpo = data.read_exact(largo, m["idle_timeout_s"])
                    t_fin = mono_ns()
                    res["t_transferencia_s"] = seconds_between(t_conn, t_fin)
                    sha = sha256_bytes(cuerpo)
                    res["payload_sha256"] = sha
                    res["truncated_hash_matches"] = bytes.fromhex(sha)[:8] == hash_trunc
                    esperado = sha256_bytes(make_payload(largo, seed))
                    res["matches_expected_payload"] = (sha == esperado)
                    if not res["truncated_hash_matches"]:
                        failures.append("SHA-256 does not match the header")
                    if not res["matches_expected_payload"]:
                        failures.append("payload does not match the seeded payload")
                except (TimeoutError, ConnectionError, ValueError) as e:
                    failures.append(f"port 8301 read failed: {e!r}")

                # Bytes extra: espera de gracia y luego cuenta todo lo recibido en 8301.
                time.sleep(m["extra_bytes_grace_s"])
                d = cmd.wait_line(RX_DISCONNECTED, m["disconnect_wait_s"], start=idx0)
                if d:
                    t_disc = d[1][0]
    res["final_state"], res["cleanup_disconnect"] = ensure_disconnected(
        cmd, m["disconnect_wait_s"], idx0, win)

    esperado_total = HEADER_LEN + res.get("declared_length", 0)
    total = sum(n for _, n in data.chunks)
    res["data_port_total_bytes"] = total
    res["data_port_expected_bytes"] = esperado_total
    if total > esperado_total:
        failures.append(f"extra bytes on port 8301: {total - esperado_total}")
    tol = m["session_tolerance_ms"] * 1_000_000
    fuera = sum(n for t, n in data.chunks
                if (t_conn is None or t < t_conn - tol)
                or (t_disc is not None and t > t_disc + tol))
    res["data_port_bytes_outside_session"] = fuera
    if fuera:
        failures.append(f"{fuera} bytes on port 8301 outside the CONNECTED session")
    res["data_port_chunks"] = [{"t_mono_ns": t, "bytes": n} for t, n in data.chunks]

    if res["final_state"] != "disconnected":
        failures.append("DISCONNECTED was not confirmed")
    res["responses"] = responses
    res["command_response_error"] = has_error(
        [l for v in responses.values() for l in v["lines"]], m.get("error_patterns", []))
    if res["command_response_error"]:
        failures.append("a command response matches error_patterns")
    res["failures"] = failures
    res["delivered_intact"] = (not failures and res.get("matches_expected_payload") is True
                                and res.get("truncated_hash_matches") is True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Mercury modem smoke client.")
    ap.add_argument("mode", choices=["send", "listen"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--phase", choices=["e0", "smoke"], default="e0")
    ap.add_argument("--tag", required=True, help="For example: E0-1_1KB, E0-2_V1, H1-1")
    ap.add_argument("--payload-size", type=int, default=200)
    ap.add_argument("--bw-when", choices=["before", "after", "none"], default="before",
                    help="before: BW before CONNECT/LISTEN ON; after: after CONNECTED "
                         "(sender) or LISTEN ON (receiver); none: do not send BW")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rlog = RunLog(cfg, args.phase, args.tag)
    m = cfg["mercury"]
    ports = (m["cmd_port"], m["data_port"])
    cmd = data = None
    env: dict = {}
    res: dict = {"mode": args.mode, "tag": args.tag, "run_id": rlog.run_id}
    exit_code = 1
    try:
        env = capture_environment(cfg, rlog, puertos=ports)
        cmd = TcpCapture("8300", m["host"], m["cmd_port"], rlog, line_mode=True)
        data = TcpCapture("8301", m["host"], m["data_port"], rlog, line_mode=False)
        res = (run_send if args.mode == "send" else run_listen)(args, cfg, rlog, cmd, data)
        exit_code = 0 if (res.get("sender_completed") or res.get("delivered_intact")) else 1
    except Exception as e:  # noqa: BLE001 - se registra el error completo
        res["exception"] = traceback.format_exc()
        res["failures"] = res.get("failures", []) + [f"exception: {e!r}"]
        rlog.log("exception", detail=res["exception"])
    finally:
        # Estado de cada socket: distingue cierre limpio, timeout y error del socket.
        res["sockets"] = {s.name: s.state() for s in (cmd, data) if s}
        for s in (cmd, data):
            if s:
                s.close()
        fin = capture_final(cfg, rlog, puertos=ports)
        res.update(evidence_issues(cfg, env, fin))
        if res["incomplete_evidence"]:
            exit_code = exit_code or 1
        ruta = rlog.save_json("result.json", res)
        rlog.log("end", result_json=str(ruta), failures=res.get("failures"))
        rlog.close()
    print(f"\nResult saved to: {ruta}")
    if res.get("incomplete_evidence"):
        print("INCOMPLETE EVIDENCE:", res["incomplete_evidence"])
    if args.mode == "send":
        print("Exit code 0 = sender session completed. It does NOT prove delivery; "
              "check the receiver outcome with smoke_cross_check.py.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

