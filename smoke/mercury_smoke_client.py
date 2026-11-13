"""Modem smoke client for profile MS-1.0 (Mercury 1.9.19 and VARA HF 4.9.0).

One invocation performs one fresh sender or receiver session for E0-1, E0-2,
E0-3 or H1. With --ack (E0-3) B writes an ACK on the data port of the same open
session and A verifies it before disconnecting. The sender reports native ARQ confirmation separately from verified
delivery. The receiver validates frame length and SHA-256. Use
smoke_cross_check.py to establish end-to-end delivery.

The file keeps its historical name (it is referenced by the packaged GUI); the
modem is selected by [modem] name in the configuration.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback

from smoke_common import (HEADER_LEN, RunLog, TcpCapture, ack_run, capture_environment,
                          capture_final, load_config, evidence_issues, make_ack, make_header,
                          make_payload, mono_ns, parse_ack, parse_header, sha256_bytes, tnc_config)

RX_CONNECTED = r"^\s*CONNECTED\b"
RX_DISCONNECTED = r"^\s*DISCONNECTED\b"
RX_BUFFER = r"^\s*BUFFER\s+(\d+)"
RX_RESP = r"^\s*(OK|WRONG)\b"
RX_SN = r"^\s*SN\s+(-?\d+(?:\.\d+)?)"
# "BITRATE (3) 321 BPS" (Mercury) and "BITRATE (4)  88 bps TX" (VARA 4.9.0): level in brackets.
RX_BITRATE = r"^\s*BITRATE\s+\((\d+)\)\s+(\d+)\s*bps(?:\s+(TX|RX))?"
INFRASTRUCTURE_EVENTS = ("MISSING SOUNDCARD",)

# Per-modem differences, kept here so that the procedure itself stays common.
#   require_ok:       a command without an OK/WRONG reply is a failure (True) or only
#                     a protocol warning (False, until the replies are confirmed in E0-2).
#   session_commands: sent once after MYCALL, before BW/CONNECT/LISTEN ON.
MODEM_PROFILES = {
    "mercury": {
        "label": "Mercury 1.9.19",
        "require_ok": True,
        "session_commands": [],
    },
    "vara": {
        "label": "VARA HF 4.9.0",
        # Confirmed on 2026-10-22 (E0-1, both PCs): VARA answers OK to MYCALL, P2P SESSION,
        # CHAT OFF, BW500, LISTEN ON, CONNECT and DISCONNECT, like Mercury.
        "require_ok": True,
        # VERSION (VARA >= 4.6.4) records the running version; P2P SESSION (>= 4.5.7)
        # selects the 4.6 s retry cycle documented for peer-to-peer links; CHAT OFF
        # (the default) keeps the timing optimised for data transfer, not chat.
        "session_commands": ["VERSION", "P2P SESSION", "CHAT OFF"],
    },
}


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


def judge_command(result: dict, label: str, failures: list[str],
                  warnings: list[str] | None = None, require_ok: bool = True) -> bool:
    """Record a failure for WRONG, and for a missing reply when require_ok is set.

    Returns True when the procedure may continue.
    """
    if result["response"] == "OK":
        return True
    if result["response"] == "WRONG":
        failures.append(f"{label}: the modem returned WRONG ({result['command']})")
        return False
    message = (f"{label}: no recognized OK/WRONG response ({result['command']}); "
               f"lines: {result['lines']}")
    if require_ok or warnings is None:
        failures.append(message)
        return False
    warnings.append(message)
    return True


def parse_connected(text: str, bandwidth_command: str, mycall: str, peer_call: str,
                    role: str) -> dict:
    """Parse and validate a CONNECTED event (same form on Mercury and VARA)."""
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


def early_session(chunks, listen_ns: int | None, lines_ns, t_conn: int | None) -> dict | None:
    """Detect a session that began before this receiver listened.

    Evidence: bytes on the data port before LISTEN ON was sent, or a DISCONNECTED
    before the CONNECTED this run waited for (2026-10-22, Mercury H1-1 at 23:44).
    """
    early_bytes = sum(n for t, n in chunks if listen_ns is not None and t < listen_ns)
    early_disconnect = any(re.match(RX_DISCONNECTED, txt) for t, txt in lines_ns
                           if t_conn is None or t < t_conn)
    if not (early_bytes or early_disconnect):
        return None
    return {"bytes_before_listen_on": early_bytes, "disconnected_before_connected": early_disconnect}


def ensure_disconnected(cmd: TcpCapture, espera_s: float, desde: int, win: float):
    """Confirm the final DISCONNECTED event, sending cleanup if required."""
    visto = cmd.wait_line(RX_DISCONNECTED, 0.1, start=desde)
    limpieza = None
    if visto is None:
        limpieza = send_command(cmd, "DISCONNECT", win)
        visto = cmd.wait_line(RX_DISCONNECTED, espera_s, start=desde)
    return ("disconnected" if visto else "not_confirmed"), limpieza


def link_stats(lines: list[str]) -> dict:
    """Summarise link observations reported on the command port (SN, BITRATE, PTT)."""
    sn = [float(m.group(1)) for line in lines for m in [re.match(RX_SN, line)] if m]
    bitrates = list(dict.fromkeys(line.strip() for line in lines
                                  if line.strip().upper().startswith("BITRATE")))
    levels = [{"level": int(m.group(1)), "bps": int(m.group(2)), "direction": m.group(3)}
              for line in lines for m in [re.match(RX_BITRATE, line, re.I)] if m]
    upper = [line.strip().upper() for line in lines]
    # VARA reports whether the link runs in registered (licensed) mode.
    link_state = ("unregistered" if "LINK UNREGISTERED" in upper
                  else "registered" if "LINK REGISTERED" in upper else None)
    return {
        "sn_db": sn,
        "sn_min_db": min(sn) if sn else None,
        "sn_max_db": max(sn) if sn else None,
        "bitrate_lines": bitrates,
        "bitrate_levels": levels,
        "first_level": levels[0]["level"] if levels else None,
        "link_state": link_state,
        "busy_on_count": upper.count("BUSY ON"),
        "ptt_on_count": sum(1 for line in lines if line.strip().upper() == "PTT ON"),
        "infrastructure_events": [line for line in lines
                                  if any(e in line.upper() for e in INFRASTRUCTURE_EVENTS)],
    }


def modem_identity(cfg: dict) -> tuple[str, dict, str]:
    modem = cfg.get("modem", {}).get("name", "mercury")
    if modem not in MODEM_PROFILES:
        raise ValueError(f"unknown modem '{modem}' in [modem] name")
    profile = MODEM_PROFILES[modem]
    arm = cfg.get("modem", {}).get("arm", profile["label"])
    return modem, profile, arm


def base_result(args, cfg, rlog, m) -> dict:
    modem, _profile, arm = modem_identity(cfg)
    return {"mode": args.mode, "tag": args.tag, "run_id": rlog.run_id,
            "protocol_version": cfg["general"].get("protocol_version", "5.0"),
            "smoke_profile": cfg["general"].get("smoke_profile", "MS-1.0"),
            "app_version": cfg["general"].get("app_version"),
            "modem": modem, "arm": arm,
            "mycall": m["mycall"], "peer_call": m["peer_call"], "bw_command": m["bw_command"],
            "bw_when": args.bw_when, "payload_bytes_requested": args.payload_size,
            "ack_mode": bool(getattr(args, "ack", False)),
            "condition": getattr(args, "condition", None),
            "deadline_s": getattr(args, "deadline_s", None),
            "modem_binary_sha256": modem_binary(cfg, modem)}


def modem_binary(cfg: dict, modem: str) -> str | None:
    """Hash of the modem binary (VARA.exe, or the official Mercury ZIP) for the pairing check."""
    if modem == "vara":
        return cfg.get("vara_session", {}).get("exe_sha256") or None
    return cfg.get("mercury_session", {}).get("zip_sha256") or None


def run_session_commands(cmd, profile, win, responses, failures, warnings) -> None:
    for text in profile["session_commands"]:
        key = "session:" + text
        responses[key] = send_command(cmd, text, win)
        if text == "VERSION":
            continue  # its reply is the version text; recorded, not judged
        judge_command(responses[key], text, failures, warnings, profile["require_ok"])


def finish(res, cmd, m, responses, failures, warnings, idx_start) -> None:
    """Common bookkeeping at the end of a sender or receiver session."""
    with cmd.cond:
        session_lines = [line[1] for line in cmd.lines[idx_start:]]
    res["link_stats"] = stats = link_stats(session_lines)
    if stats["infrastructure_events"]:
        res["infrastructure_fault"] = True
        failures.append("infrastructure fault reported by the modem: "
                        + "; ".join(stats["infrastructure_events"]))
    res["responses"] = responses
    res["command_response_error"] = has_error(
        [l for v in responses.values() for l in v["lines"]], m.get("error_patterns", []))
    if res["command_response_error"]:
        failures.append("a command response matches error_patterns")
    res["protocol_warnings"] = warnings
    res["failures"] = failures


BUFFER0_MISSING = "BUFFER 0 was not observed after writing (timeout or socket closed)"


def phase1_sender_judgement(res: dict) -> None:
    """Phase 1 (design section 4), sender side. The raw fields (failures, sender_completed,
    arq_confirmed) are kept unchanged; this adds the derived judgement. A's part of a counted
    success: B's ACK verified (same run and payload hash) before the deadline, no
    infrastructure or procedure fault, and no failure other than a missing BUFFER 0, which
    is then a warning: B's ACK carries the payload's SHA-256 and so proves the delivery.
    The pair's counted_success (cross-check) also needs B's verified payload."""
    failures = res.get("failures") or []
    others = [f for f in failures if f != BUFFER0_MISSING]
    res["buffer0_observed"] = bool(res.get("arq_confirmed"))
    res["sender_counted_success"] = bool(
        res.get("ack_verified") is True and not res.get("deadline_reached")
        and not res.get("infrastructure_fault") and not res.get("procedure_fault") and not others)
    res["phase1_warnings"] = (["BUFFER 0 not observed; B's verified ACK shows the delivery (warning, "
                               "not a failure)"] if res["sender_counted_success"] and BUFFER0_MISSING in failures
                              else [])


def apply_deadline(m: dict, args, role: str) -> dict:
    """Campaign attempts (protocol v5.3): every wait is bounded by the attempt deadline."""
    deadline = getattr(args, "deadline_s", None)
    if not deadline:
        return m
    m = dict(m)
    if role == "send":
        m.update(connect_timeout_s=deadline, transfer_timeout_s=deadline, ack_timeout_s=deadline)
    else:
        # B listens until A's deadline has surely passed, and tolerates long ARQ pauses.
        m.update(listen_timeout_s=deadline + 300, idle_timeout_s=deadline, ack_timeout_s=deadline)
    return m


def wait_for_ack(res: dict, data: TcpCapture, header: bytes, payload_sha: str,
                 t_write_ns: int, timeout_s: float, failures: list[str]) -> None:
    """E0-3 sender: read B's ACK from the open session and check that it names this frame."""
    res.update({"ack_verified": False, "ack_line": None,
                "ack_expected": {"run": ack_run(header), "sha256_16": payload_sha[:16]}})
    try:
        line = data.read_until(b"\n", timeout_s)
    except (TimeoutError, ConnectionError, ValueError) as e:
        failures.append(f"E0-3: no ACK from B in the open session ({e})")
        return None
    t_ack = mono_ns()
    res["ack_line"] = line.decode("ascii", "replace").strip()
    res["ack_round_trip_s"] = seconds_between(t_write_ns, t_ack)
    ack = parse_ack(line)
    if ack is None:
        failures.append(f"E0-3: B's reply is not an ACK: {res['ack_line']!r}")
    elif ack != res["ack_expected"]:
        failures.append(f"E0-3: the ACK names another frame: {ack} (expected {res['ack_expected']})")
    else:
        res["ack_verified"] = True
    extra = data.pending()
    if extra:
        failures.append(f"E0-3: {extra} unexpected bytes after the ACK on the data port")
    return t_ack


def run_send(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = tnc_config(cfg)
    _modem, profile, _arm = modem_identity(cfg)
    m = apply_deadline(m, args, "send")
    req = profile["require_ok"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    payload = make_payload(args.payload_size, seed)
    header = make_header(payload)
    res = base_result(args, cfg, rlog, m)
    res.update({"destination": m["peer_call"], "payload_bytes": len(payload),
                "payload_sha256": sha256_bytes(payload), "header_hex": header.hex(),
                "arq_confirmed": False, "delivered_intact": None})
    failures: list[str] = []
    warnings: list[str] = []
    responses: dict[str, dict] = {}
    idx_start = cmd.nlines()

    def enviar_bw():
        responses["bw"] = send_command(cmd, m["bw_command"], win)
        res["bw_accepted"] = responses["bw"]["response"] == "OK"
        if args.bw_when == "before" and responses["bw"]["response"] != "OK":
            judge_command(responses["bw"], "BW before CONNECT", failures, warnings, req)

    responses["mycall"] = send_command(cmd, f"MYCALL {m['mycall']}", win)
    judge_command(responses["mycall"], "MYCALL", failures, warnings, req)
    run_session_commands(cmd, profile, win, responses, failures, warnings)
    if args.bw_when == "before":
        enviar_bw()

    idx0 = cmd.nlines()
    t_cmd = mono_ns()
    t_conn = None
    deadline_s = getattr(args, "deadline_s", None)
    end_ns = t_cmd + int(deadline_s * 1e9) if deadline_s else None

    def bounded(limit_s: float) -> float:
        if end_ns is None:
            return limit_s
        return max(0.0, min(limit_s, (end_ns - mono_ns()) / 1e9))

    if deadline_s:
        res["deadline_s"] = deadline_s
    if not failures:
        responses["connect"] = send_command(cmd, f"CONNECT {m['mycall']} {m['peer_call']}", win)
        if judge_command(responses["connect"], "CONNECT", failures, warnings, req):
            r = cmd.wait_line(RX_CONNECTED, bounded(m["connect_timeout_s"]), start=idx0)
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
                    data.send(header + payload, label="header+payload")
                except OSError as e:
                    failures.append(f"error while writing the data port: {e!r}")
                t_w1 = mono_ns()
                res["write_s"] = seconds_between(t_w0, t_w1)
                if args.ack:
                    # B's ACK may arrive before the modem reports BUFFER 0: stop waiting then.
                    b0 = None
                    while bounded(m["transfer_timeout_s"]) > 0:
                        b0 = cmd.wait_line_after(r"^\s*BUFFER\s+0\b", min(0.5, bounded(1e9)),
                                                 start=idx_w, t_min_ns=t_w0)
                        if b0 is not None or data.holds(b"\n") or data.closed:
                            break
                else:
                    b0 = cmd.wait_line_after(r"^\s*BUFFER\s+0\b", bounded(m["transfer_timeout_s"]),
                                             start=idx_w, t_min_ns=t_w0)
                with cmd.cond:
                    buffers_ns = [(t, txt) for t, txt in cmd.lines[idx_w:]
                                  if re.search(RX_BUFFER, txt)]
                res["buffer_lines"] = [(seconds_between(t_w0, t), txt) for t, txt in buffers_ns]
                # Observation, not a pass criterion: a positive BUFFER preceding zero
                # shows a current buffer transition rather than a delayed stale value.
                res["positive_buffer_before_zero"] = has_positive_buffer(
                    buffers_ns, t_w0, b0[1][0] if b0 else None)
                if b0 is not None:
                    res["arq_confirmed"] = True
                    res["t_transferencia_s"] = seconds_between(t_w0, b0[1][0])
                if args.ack:
                    t_ack = wait_for_ack(res, data, header, res["payload_sha256"], t_w0,
                                         bounded(m.get("ack_timeout_s", 300)), failures)
                    if res.get("ack_verified"):
                        res["delivery_time_s"] = seconds_between(t_cmd, t_ack)
                if b0 is None:
                    # Raw evidence, kept as in every earlier version. Phase 1 judges it below
                    # (counted_success) as a warning when B's ACK was verified.
                    failures.append(BUFFER0_MISSING)
                # The session is active, so DISCONNECT must be accepted.
                responses["disconnect"] = send_command(cmd, "DISCONNECT", win)
                judge_command(responses["disconnect"], "DISCONNECT", failures, warnings, req)
    if end_ns is not None and not res.get("ack_verified") and mono_ns() >= end_ns:
        res["deadline_reached"] = True
        failures.append(f"deadline of {deadline_s:.0f} s reached before a verified ACK")
    res["connection_s"] = seconds_between(t_cmd, t_conn)
    res["final_state"], res["cleanup_disconnect"] = ensure_disconnected(
        cmd, m["disconnect_wait_s"], idx0, win)
    if res["final_state"] != "disconnected":
        failures.append("DISCONNECTED was not confirmed")
    finish(res, cmd, m, responses, failures, warnings, idx_start)
    res["sender_completed"] = not failures
    if args.ack:
        res["sender_completed"] = res["sender_completed"] and res.get("ack_verified") is True
    if args.condition:
        phase1_sender_judgement(res)
    return res


def send_ack(res: dict, cmd: TcpCapture, data: TcpCapture, header: bytes, sha: str,
             m: dict, failures: list[str]) -> None:
    """E0-3 receiver: write the ACK in the same session and record its ARQ confirmation."""
    ack = make_ack(header, sha)
    idx = cmd.nlines()
    t0 = mono_ns()
    try:
        data.send(ack, label="ack")
    except OSError as e:
        failures.append(f"E0-3: the ACK could not be written: {e!r}")
        res["ack_sent"] = False
        return
    res["ack_sent"] = True
    res["ack_line"] = ack.decode("ascii").strip()
    # Recorded, not judged: the sender's verification is the pass criterion.
    b0 = cmd.wait_line_after(r"^\s*BUFFER\s+0\b", m.get("ack_timeout_s", 300), start=idx, t_min_ns=t0)
    res["ack_arq_confirmed"] = b0 is not None
    res["ack_arq_s"] = seconds_between(t0, b0[1][0]) if b0 else None


def run_listen(args, cfg, rlog, cmd: TcpCapture, data: TcpCapture) -> dict:
    m = tnc_config(cfg)
    _modem, profile, _arm = modem_identity(cfg)
    m = apply_deadline(m, args, "listen")
    req = profile["require_ok"]
    win = m["cmd_response_window_s"]
    seed = cfg["general"]["payload_seed"]
    res = base_result(args, cfg, rlog, m)
    res.update({"delivered_intact": False, "arq_confirmed": None})
    failures: list[str] = []
    warnings: list[str] = []
    responses: dict[str, dict] = {}
    idx_start = cmd.nlines()

    def enviar_bw():
        responses["bw"] = send_command(cmd, m["bw_command"], win)
        res["bw_accepted"] = responses["bw"]["response"] == "OK"
        if args.bw_when == "before" and responses["bw"]["response"] != "OK":
            judge_command(responses["bw"], "BW before LISTEN ON", failures, warnings, req)

    responses["mycall"] = send_command(cmd, f"MYCALL {m['mycall']}", win)
    judge_command(responses["mycall"], "MYCALL", failures, warnings, req)
    run_session_commands(cmd, profile, win, responses, failures, warnings)
    if args.bw_when == "before":
        enviar_bw()
    idx0 = cmd.nlines()
    t_conn = t_disc = None
    if not failures:
        responses["listen"] = send_command(cmd, "LISTEN ON", win)
        if judge_command(responses["listen"], "LISTEN ON", failures, warnings, req):
            rlog.log("waiting_for_connection")
            r = cmd.wait_line(RX_CONNECTED, m["listen_timeout_s"], start=idx0)
            if r is None:
                failures.append("no CONNECTED event was observed")
            else:
                t_conn = r[1][0]
                res["connected"] = parse_connected(r[1][1], m["bw_command"], m["mycall"],
                                                   m["peer_call"], "listen")
                failures += connected_failures(res["connected"])
                if args.bw_when == "after":   # as on the sender: after CONNECTED
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
                    if args.ack and not failures:
                        send_ack(res, cmd, data, cab, sha, m, failures)
                except (TimeoutError, ConnectionError, ValueError) as e:
                    failures.append(f"data port read failed: {e!r}")

                # Extra bytes: wait a grace period, then count everything received.
                time.sleep(m["extra_bytes_grace_s"])
                d = cmd.wait_line(RX_DISCONNECTED, m["disconnect_wait_s"]
                                  + (m.get("ack_timeout_s", 300) if args.ack else 0), start=idx0)
                if d:
                    t_disc = d[1][0]
    res["final_state"], res["cleanup_disconnect"] = ensure_disconnected(
        cmd, m["disconnect_wait_s"], idx0, win)
    if "listen" in responses:
        # Mercury 1.9.19 stays LISTENING after the TCP client leaves (listen_enabled is
        # kept) and buffers what a caller sends until the next client connects. Release
        # it, so that a sender started too early fails cleanly instead of delivering
        # into the next run.
        responses["listen_off"] = send_command(cmd, "LISTEN OFF", win)
        if responses["listen_off"]["response"] != "OK":
            warnings.append(f"LISTEN OFF: no OK ({responses['listen_off']['lines']})")

    esperado_total = HEADER_LEN + res.get("declared_length", 0)
    total = sum(n for _, n in data.chunks)
    res["data_port_total_bytes"] = total
    res["data_port_expected_bytes"] = esperado_total
    if total > esperado_total:
        failures.append(f"extra bytes on the data port: {total - esperado_total}")
    tol = m["session_tolerance_ms"] * 1_000_000
    fuera = sum(n for t, n in data.chunks
                if (t_conn is None or t < t_conn - tol)
                or (t_disc is not None and t > t_disc + tol))
    res["data_port_bytes_outside_session"] = fuera
    if fuera:
        failures.append(f"{fuera} bytes on the data port outside the CONNECTED session")
    res["data_port_chunks"] = [{"t_mono_ns": t, "bytes": n} for t, n in data.chunks]
    # A session that began before this PC listened (the sender started first, or the
    # modem was still listening from an earlier run). Not a modem failure.
    with cmd.cond:
        lines_ns = list(cmd.lines[idx_start:])
    early = early_session(data.chunks, cmd.tx_times.get("LISTEN ON"), lines_ns, t_conn)
    if early:
        res["procedure_fault"] = True
        res["procedure_fault_detail"] = early
        failures.insert(0, "PROCEDURE: the modem already had a session before this PC was "
                           "listening (PC A started before PC B showed waiting_for_connection, "
                           "or the modem was still listening from an earlier run). Not a modem "
                           "failure; repeat this test, PC B first")

    if res["final_state"] != "disconnected":
        failures.append("DISCONNECTED was not confirmed")
    finish(res, cmd, m, responses, failures, warnings, idx_start)
    res["delivered_intact"] = (not failures and res.get("matches_expected_payload") is True
                               and res.get("truncated_hash_matches") is True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Modem smoke client (Mercury or VARA HF).")
    ap.add_argument("mode", choices=["send", "listen"])
    ap.add_argument("--config", default="smoke_config.toml")
    ap.add_argument("--phase", choices=["e0", "smoke", "p1"], default="e0")
    ap.add_argument("--tag", required=True, help="For example: E0-1_1KB, E0-2_V1, H1-1")
    ap.add_argument("--payload-size", type=int, default=200)
    ap.add_argument("--deadline-s", type=float, default=None,
                    help="Phase 1 attempt deadline from CONNECT (sender) and listening bound (receiver)")
    ap.add_argument("--condition", default=None, help="Phase 1 channel condition, recorded in the result")
    ap.add_argument("--ack", action="store_true",
                    help="E0-3: B writes an ACK in the same session; A verifies it before DISCONNECT")
    ap.add_argument("--bw-when", choices=["before", "after", "none"], default="before",
                    help="before: BW before CONNECT/LISTEN ON; after: after CONNECTED; "
                         "none: do not send BW")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rlog = RunLog(cfg, args.phase, args.tag)
    m = tnc_config(cfg)
    ports = (m["cmd_port"], m["data_port"])
    cmd = data = None
    env: dict = {}
    res: dict = {"mode": args.mode, "tag": args.tag, "run_id": rlog.run_id}
    exit_code = 1
    try:
        env = capture_environment(cfg, rlog, puertos=ports)
        cmd = TcpCapture(str(m["cmd_port"]), m["host"], m["cmd_port"], rlog, line_mode=True)
        data = TcpCapture(str(m["data_port"]), m["host"], m["data_port"], rlog, line_mode=False)
        res = (run_send if args.mode == "send" else run_listen)(args, cfg, rlog, cmd, data)
        exit_code = 0 if (res.get("sender_completed") or res.get("delivered_intact")
                          or res.get("sender_counted_success")) else 1
    except Exception as e:  # noqa: BLE001 - the full error is recorded
        res["exception"] = traceback.format_exc()
        res["failures"] = res.get("failures", []) + [f"exception: {e!r}"]
        rlog.log("exception", detail=res["exception"])
    finally:
        # Socket state distinguishes a clean close, a timeout and a socket error.
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
