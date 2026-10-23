"""A two-endpoint Mercury simulator for automated tests; it is not Mercury."""
from __future__ import annotations

import socket
import sys
import threading
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
STATE: dict[str, dict] = {"A": {}, "B": {}}


def start_endpoint(name: str, peer: str, command_port: int, data_port: int) -> None:
    listeners = []
    for port in (command_port, data_port):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        listeners.append(listener)

    def serve() -> None:
        # One session per client connection, any number of times (one per test).
        while True:
            command_socket, _ = listeners[0].accept()
            data_socket, _ = listeners[1].accept()
            STATE[name].update(command_socket=command_socket, data_socket=data_socket)
            try:
                session(command_socket, data_socket)
            except OSError:
                pass

    def session(command_socket, data_socket) -> None:

        def relay_data() -> None:
            while True:
                payload = data_socket.recv(65536)
                if not payload:
                    return
                if MODE == "truncated":
                    payload = payload[: len(payload) // 2]
                STATE[peer]["data_socket"].sendall(payload)
                if MODE == "extra":
                    time.sleep(0.3)
                    STATE[peer]["data_socket"].sendall(b"XX")
                time.sleep(0.2)
                command_socket.sendall(b"BUFFER 0\r")

        threading.Thread(target=relay_data, daemon=True).start()
        buffer = b""
        while True:
            received = command_socket.recv(4096)
            if not received:
                return
            buffer += received
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                parts = line.decode().split()
                if MODE == "wrong_mycall" and parts[0] == "MYCALL":
                    command_socket.sendall(b"WRONG\r")
                    continue
                if MODE == "silent" and parts[0] == "MYCALL":
                    continue
                command_socket.sendall(
                    b"WRONG\r" if MODE == "disconnect_wrong" and parts[0] == "DISCONNECT" else b"OK\r"
                )
                if parts[0] == "CONNECT":
                    time.sleep(0.3)
                    for endpoint in (name, peer):
                        bandwidth = "2300" if MODE == "wrong_bandwidth" else (
                            "" if MODE == "short_connected" else "500"
                        )
                        source, destination = ("A", "B") if MODE != "wrong_callsign" else ("X", "Y")
                        STATE[endpoint]["command_socket"].sendall(
                            f"CONNECTED CALL-{source} CALL-{destination} {bandwidth}\r".encode()
                        )
                if parts[0] == "DISCONNECT":
                    for endpoint in (name, peer):
                        STATE[endpoint]["command_socket"].sendall(b"DISCONNECTED\r")

    threading.Thread(target=serve, daemon=True).start()


start_endpoint("A", "B", 18300, 18301)
start_endpoint("B", "A", 19300, 19301)
time.sleep(900)
