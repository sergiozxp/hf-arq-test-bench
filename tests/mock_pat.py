"""API HTTP de Pat SIMULADA solo para probar la lógica del script (no es Pat).
Los nombres de campos del JSON (MID, Subject, Files, Name, Body) son una suposición."""
import json, sys, uuid
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, HTTPServer

OUT, INB = [], []
MODO = sys.argv[2] if len(sys.argv) > 2 else "ok"


class H(BaseHTTPRequestHandler):
    def _r(self, code, body=b"", tipo="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); datos = self.rfile.read(n)
        if self.path == "/api/mailbox/out":
            msg = message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode()
                                     + b"\r\n\r\n" + datos)
            m = {"MID": uuid.uuid4().hex[:12].upper(), "Subject": "", "Body": "", "Files": [],
                 "To": [], "_c": {}}
            for p in msg.walk():
                nom = p.get_param("name", header="content-disposition")
                if nom is None: continue
                fn = p.get_filename(); pay = p.get_payload(decode=True)
                if fn:
                    m["Files"].append({"Name": fn, "Size": len(pay)}); m["_c"][fn] = pay
                elif nom == "subject": m["Subject"] = pay.decode()
                elif nom == "body": m["Body"] = pay.decode()
                elif nom == "to": m["To"].append(pay.decode())
            OUT.append(m); return self._r(200, {"ok": True})
        if self.path.startswith("/api/connect"):
            if MODO == "fallo500": return self._r(500, b"session failed", "text/plain")
            INB.extend(OUT * (2 if MODO == 'duplicado' else 1)); OUT.clear()   # "entrega" simulada
            return self._r(200, {"NumReceived": len(INB)})
        if self.path == "/api/disconnect": return self._r(200, {})
        self._r(404)

    def do_GET(self):
        vis = lambda L: [{k: v for k, v in m.items() if k != "_c"} for m in L]
        if self.path == "/api/mailbox/out": return self._r(200, vis(OUT))
        if self.path == "/api/mailbox/in": return self._r(500 if MODO == "inbox500" else 200, vis(INB))
        if self.path.startswith("/api/mailbox/in/"):
            partes = self.path.split("/"); mid, fn = partes[4], "/".join(partes[5:])
            for m in INB:
                if m["MID"] == mid and fn in m["_c"]:
                    c = m["_c"][fn]
                    if MODO == "corrupto": c = c[:-1] + b"\x00"
                    return self._r(200, c, "application/octet-stream")
        self._r(404, b"no existe", "text/plain")


HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
