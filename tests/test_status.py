"""Level-meter tests against a local WebSocket server that imitates Mercury's status feed."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import mercury_status as ms

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def check(condition: bool, message: str) -> None:
    print(("OK   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        raise SystemExit(1)


def frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def status(peak, snr, sync=True, audio_ok=True, error=""):
    return json.dumps({"type": "status", "bitrate": 321, "snr": snr, "sync": sync, "direction": "rx",
                       "tx_peak_dbfs": peak, "audio_ok": audio_ok, "audio_error": error,
                       "arq_tx_mode": "DATAC3", "arq_rx_mode": "DATAC3",
                       "bytes_transmitted": 0, "bytes_received": 0}).encode()


pong_received = threading.Event()


def serve(listener: socket.socket) -> None:
    conn, _ = listener.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += conn.recv(1024)
    assert b"GET /websocket " in request
    key = [l.split(b":", 1)[1].strip() for l in request.split(b"\r\n")
           if l.lower().startswith(b"sec-websocket-key")][0]
    accept = base64.b64encode(hashlib.sha1(key + GUID.encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    conn.sendall(frame(0x9, b"ping"))                                   # must be answered
    conn.sendall(frame(0x1, b'{"type":"capture_dev_list"}'))            # not a status
    conn.sendall(frame(0x2, b"\x00\x01"))                               # binary spectrum
    first = status(-6.0, 20.0)
    conn.sendall(frame(0x1, first[:10], fin=False) + frame(0x0, first[10:]))   # fragmented
    for message in (status(-3.0, 25.0), status(-1.0, 12.5),
                    status(-120.0, 0.0, sync=False, audio_ok=False, error="capture device lost")):
        time.sleep(0.05)
        conn.sendall(frame(0x1, message))
    conn.settimeout(3)
    try:
        data = conn.recv(64)
        if data and data[0] & 0x0F == 0xA and data[1] & 0x80:            # masked pong
            pong_received.set()
    except socket.timeout:
        pass
    time.sleep(0.3)
    conn.sendall(frame(0x8, b""))
    conn.close()


listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
port = listener.getsockname()[1]
threading.Thread(target=serve, args=(listener,), daemon=True).start()

log = Path(tempfile.mkdtemp(prefix="hf_arq_status_")) / "mercury_status.jsonl"
seen: list[dict] = []
start = ms.utc_now()
monitor = ms.StatusMonitor(port, log_path=log, on_status=seen.append).start()
deadline = time.time() + 10
while time.time() < deadline and len(seen) < 4:
    time.sleep(0.05)
end = ms.utc_now()
monitor.stop()

check(len(seen) == 4, "four status messages received (fragmented one included, others ignored)")
check(seen[0]["tx_peak_dbfs"] == -6.0 and seen[0]["snr"] == 20.0, "fragmented message reassembled")
check(pong_received.is_set(), "ping answered with a masked pong")
check(len(log.read_text().splitlines()) == 4, "every status is logged")
summary = ms.summarize_window(log, start, end)
check(summary["available"] and summary["samples"] == 4, "test window summarised")
check(summary["tx_peak_max_dbfs"] == -1.0 and summary["tx_peak_over_limit"], "peak above 80 % is flagged")
check(summary["rx_snr_min_db"] == 12.5 and summary["rx_snr_max_db"] == 25.0, "SNR range uses synced frames only")
check(summary["audio_ok"] is False and summary["audio_errors"] == ["capture device lost"],
      "a sound card failure is reported")
check(ms.summarize_window(log, "2000-01-01", "2000-01-02")["available"] is False, "an empty window is marked")
check(abs(ms.PEAK_LIMIT_DBFS - 20 * __import__("math").log10(0.8)) < 0.01, "limit is 80 % of full scale")
print("ALL STATUS TESTS PASSED")
