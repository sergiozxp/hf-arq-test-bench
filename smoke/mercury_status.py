"""Read Mercury's live status for the level meter.

Mercury 1.9.19 publishes a JSON status message every 0.5 s on a WebSocket
(ws://127.0.0.1:<ui_port>/websocket) when ui_enabled = true. The fields used here
(from gui_interface/ui_status.c, tag v1.9.19):

  tx_peak_dbfs  digital peak of the last transmitted burst, before the sound card
                and before the Windows volume (0 dBFS = full scale)
  snr           SNR of the last received frame, in dB
  sync          true while a frame is being decoded
  direction     "tx" or "rx"
  audio_ok      false when a sound card thread failed (audio_error says why)
  bitrate, arq_tx_mode, arq_rx_mode, bytes_transmitted, bytes_received

The client below is a minimal RFC 6455 client (text frames, ping/pong, close) so
that the packaged GUI needs no extra library. It only reads; it never sends
commands to Mercury.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

UI_PORT = 10000
PATH = "/websocket"
# 80 % of full scale, the limit set by the smoke protocol, is 20*log10(0.8) dBFS.
PEAK_LIMIT_DBFS = -1.94
STATUS_FIELDS = ("tx_peak_dbfs", "snr", "sync", "direction", "audio_ok", "audio_error",
                 "bitrate", "arq_tx_mode", "arq_rx_mode", "bytes_transmitted", "bytes_received")


class WebSocketClosed(Exception):
    pass


class Connection:
    """A socket plus the bytes already received but not yet consumed.

    The server may send frames in the same TCP segment as its handshake reply
    (Mercury sends device lists as soon as a client connects), so the bytes that
    follow the HTTP header must be kept, not discarded.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def recv_exact(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketClosed("connection closed")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:n])
        del self.buffer[:n]
        return data

    def sendall(self, data: bytes) -> None:
        self.sock.sendall(data)


def handshake(conn: Connection, host: str, port: int, path: str = PATH) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
    conn.sendall(request.encode("ascii"))
    while b"\r\n\r\n" not in conn.buffer:
        chunk = conn.sock.recv(4096)
        if not chunk:
            raise WebSocketClosed("no handshake response")
        conn.buffer.extend(chunk)
        if len(conn.buffer) > 65536:
            raise WebSocketClosed("handshake response too long")
    end = conn.buffer.index(b"\r\n\r\n") + 4
    response = bytes(conn.buffer[:end])
    del conn.buffer[:end]  # anything after the header is already WebSocket data
    status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
    if " 101 " not in status_line + " ":
        raise WebSocketClosed(f"handshake refused: {status_line}")


def _send_frame(sock, opcode: int, payload: bytes = b"") -> None:
    """Send one masked client frame (clients must mask, RFC 6455 5.3)."""
    mask = os.urandom(4)
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + mask + masked)


def read_message(conn: Connection) -> tuple[int, bytes]:
    """Return (opcode, payload) of the next complete data message; answer pings."""
    message, message_opcode = bytearray(), None
    while True:
        b0, b1 = conn.recv_exact(2)
        fin, opcode = b0 & 0x80, b0 & 0x0F
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", conn.recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", conn.recv_exact(8))[0]
        mask = conn.recv_exact(4) if b1 & 0x80 else None
        payload = conn.recv_exact(length) if length else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise WebSocketClosed("server closed the connection")
        if opcode == 0x9:
            _send_frame(conn, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode in (0x1, 0x2):
            message, message_opcode = bytearray(payload), opcode
        elif opcode == 0x0 and message_opcode is not None:
            message.extend(payload)
        if fin and message_opcode is not None:
            return message_opcode, bytes(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StatusMonitor:
    """Background reader of Mercury's status; keeps the latest values and a log file."""

    def __init__(self, port: int = UI_PORT, host: str = "127.0.0.1",
                 log_path: str | Path | None = None, on_status=None):
        self.host, self.port = host, port
        self.log_path = Path(log_path) if log_path else None
        self.on_status = on_status
        self.latest: dict | None = None
        self.connected = False
        self.error: str | None = None
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "StatusMonitor":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._thread.join(timeout=3)

    def _run(self) -> None:
        log = open(self.log_path, "a", encoding="utf-8") if self.log_path else None
        try:
            while not self._stop.is_set():
                try:
                    self._sock = socket.create_connection((self.host, self.port), timeout=5)
                    conn = Connection(self._sock)
                    handshake(conn, self.host, self.port)
                    # Block without a timeout: a timeout in the middle of a frame would
                    # lose its first bytes. stop() closes the socket to end the read.
                    self._sock.settimeout(None)
                    self.connected, self.error = True, None
                    while not self._stop.is_set():
                        opcode, payload = read_message(conn)
                        if opcode != 0x1:
                            continue
                        try:
                            message = json.loads(payload.decode("utf-8", "replace"))
                        except ValueError:
                            continue
                        if message.get("type") != "status":
                            continue
                        status = {key: message.get(key) for key in STATUS_FIELDS}
                        status["t_utc"] = utc_now()
                        self.latest = status
                        if log:
                            log.write(json.dumps(status) + "\n")
                            log.flush()
                        if self.on_status:
                            self.on_status(status)
                except (OSError, WebSocketClosed) as error:
                    self.connected, self.error = False, str(error)
                    if self._sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                    self._stop.wait(2)
        finally:
            self.connected = False
            if log:
                log.close()


def summarize_window(log_path: str | Path, start_utc: str, end_utc: str) -> dict:
    """Summarise the status log between two UTC instants (ISO 8601)."""
    peaks, snrs, errors, samples = [], [], set(), 0
    audio_ok = True
    try:
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return {"available": False, "reason": repr(error)}
    for line in lines:
        try:
            status = json.loads(line)
        except ValueError:
            continue
        if not start_utc <= status.get("t_utc", "") <= end_utc:
            continue
        samples += 1
        peak = status.get("tx_peak_dbfs")
        if isinstance(peak, (int, float)) and peak > -120:
            peaks.append(float(peak))
        snr = status.get("snr")
        if status.get("sync") and isinstance(snr, (int, float)):
            snrs.append(float(snr))
        if status.get("audio_ok") is False:
            audio_ok = False
            errors.add(status.get("audio_error") or "unknown")
    tx_peak = max(peaks) if peaks else None
    return {
        "available": samples > 0, "samples": samples,
        "tx_peak_max_dbfs": tx_peak,
        "tx_peak_over_limit": tx_peak is not None and tx_peak > PEAK_LIMIT_DBFS,
        "peak_limit_dbfs": PEAK_LIMIT_DBFS,
        "rx_snr_min_db": min(snrs) if snrs else None,
        "rx_snr_max_db": max(snrs) if snrs else None,
        "audio_ok": audio_ok, "audio_errors": sorted(errors),
        "window_utc": [start_utc, end_utc],
        "note": "tx_peak is Mercury's digital output peak, before the Windows volume and "
                "the sound card; check the analog level on the other PC.",
    }


if __name__ == "__main__":  # manual check: python mercury_status.py [port]
    import sys
    monitor = StatusMonitor(int(sys.argv[1]) if len(sys.argv) > 1 else UI_PORT,
                            on_status=lambda s: print(s, flush=True)).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()
