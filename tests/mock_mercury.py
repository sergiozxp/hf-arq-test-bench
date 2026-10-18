"""Mercury SIMULADO solo para probar la lógica de los scripts (no es Mercury).
Semántica tomada de la descripción de docs/TNC.md: comandos con \\r, datos sin framing,
eventos CONNECTED / DISCONNECTED / BUFFER. Modo extra: 'extra' envía bytes sobrantes."""
import socket, sys, threading, time

MODO = sys.argv[1] if len(sys.argv) > 1 else "ok"
estado = {"A": {}, "B": {}}

def inst(nombre, otro, cport, dport):
    ls = []
    for p in (cport, dport):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", p)); s.listen(1); ls.append(s)
    def atender():
        cs, _ = ls[0].accept(); ds, _ = ls[1].accept()
        estado[nombre].update(cs=cs, ds=ds)
        def datos():
            while True:
                b = ds.recv(65536)
                if not b: return
                o = estado[otro]
                if MODO == "corto":      # simula payload truncado
                    b = b[:len(b) // 2]
                o["ds"].sendall(b)
                if MODO == "extra":
                    time.sleep(0.3); o["ds"].sendall(b"XX")
                time.sleep(0.2)
                cs.sendall(b"BUFFER 0\r")
        threading.Thread(target=datos, daemon=True).start()
        buf = b""
        while True:
            d = cs.recv(4096)
            if not d: return
            buf += d
            while b"\r" in buf:
                l, buf = buf.split(b"\r", 1); c = l.decode().split()
                if MODO == "wrongmycall" and c[0] == "MYCALL":
                    cs.sendall(b"WRONG\r"); continue
                if MODO == "silencio" and c[0] == "MYCALL":
                    continue
                cs.sendall(b"WRONG\r" if (MODO == "disconnectwrong" and c[0] == "DISCONNECT") else b"OK\r")
                if c[0] == "CONNECT":
                    time.sleep(0.3)
                    for n in (nombre, otro):
                        ancho = "2300" if MODO == "ancho" else ("" if MODO == "connectedcorto" else "500")
                        a_, b_ = ("A", "B") if MODO != "indicativo" else ("X", "Y")
                        estado[n]["cs"].sendall(f"CONNECTED CALL-{a_} CALL-{b_} {ancho}\r".encode())
                if c[0] == "DISCONNECT":
                    for n in (nombre, otro):
                        estado[n]["cs"].sendall(b"DISCONNECTED\r")
    threading.Thread(target=atender, daemon=True).start()

inst("A", "B", 18300, 18301)
inst("B", "A", 19300, 19301)
time.sleep(60)
