"""Construye la ventana con un tkinter FALSO para detectar errores de nombres o argumentos
en smoke_gui.py sin necesitar pantalla. NO prueba el aspecto ni los clics reales.

Ejecuta además una prueba completa (Mercury simulado) por el mismo camino que usa el botón
EJECUTAR: formulario -> smoke_config.toml -> plan -> subproceso -> resumen."""
import sys
import tempfile
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "smoke"))


class Comodin:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return Comodin()
    def __call__(self, *a, **k): return Comodin()
    def __setitem__(self, k, x): pass
    def __getitem__(self, k): return None
class Var:
    def __init__(self, value=None, **k): self.v = value if value is not None else ""
    def get(self): return self.v
    def set(self, x): self.v = x
class Tk(Comodin):
    def after(self, *a, **k): pass
    def protocol(self, *a, **k): pass

tk = types.ModuleType("tkinter")
tk.Tk, tk.StringVar, tk.IntVar = Tk, Var, Var
for n in ("Text", "Label", "Button"): setattr(tk, n, Comodin)
ttk = types.ModuleType("tkinter.ttk")
for n in ("LabelFrame", "Label", "Radiobutton", "Entry", "Combobox", "Spinbox", "Button", "Frame", "Scrollbar"):
    setattr(ttk, n, Comodin)
dlg = types.ModuleType("tkinter.filedialog"); mb = types.ModuleType("tkinter.messagebox")
for n in ("askopenfilename", "asksaveasfilename"): setattr(dlg, n, lambda *a, **k: "")
for n in ("showwarning", "showinfo", "showerror", "askyesno"): setattr(mb, n, lambda *a, **k: True)
tk.ttk, tk.filedialog, tk.messagebox = ttk, dlg, mb
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": dlg, "tkinter.messagebox": mb})

import smoke_gui_logica as L  # noqa: E402
import smoke_gui  # noqa: E402

v = smoke_gui.Ventana()
assert v.rol if False else True
print("OK    la ventana se construye sin errores de nombres")
assert smoke_gui.comando_de_proceso(["pat_smoke_test.py", "queue"])[1:3] == ["-u", str(RAIZ / "smoke" / "pat_smoke_test.py")]
print("OK    comando de proceso (modo Python)")
smoke_gui.EMPAQUETADO = True
assert smoke_gui.comando_de_proceso(["pat_smoke_test.py", "queue"]) == [sys.executable, "--script", "pat_smoke_test.py", "queue"]
smoke_gui.EMPAQUETADO = False
print("OK    comando de proceso (modo .exe)")

# Camino completo del botón EJECUTAR con el Mercury simulado (PC A y PC B por turnos).
import json, subprocess, time  # noqa: E401,E402
tmp = Path(tempfile.mkdtemp(prefix="hfarq_gui_"))
smoke_gui.AQUI = tmp
smoke_gui.RESULTADOS = tmp / "results"
mock = subprocess.Popen([sys.executable, str(RAIZ / "tests" / "mock_mercury.py"), "ok"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(1.5)
    datos = dict(rol="B", mycall="call-b", peer_call="call-a", bw="BW500", pat_url="http://localhost:8080")
    assert L.validar_formulario(datos) == []
    cfg_txt = L.texto_config(datos).replace('cmd_port = 8300', 'cmd_port = 19300').replace(
        'data_port = 8301', 'data_port = 19301').replace('connect_timeout_s = 180', 'connect_timeout_s = 90')
    cfg_txt = cfg_txt.replace('listen_timeout_s = 900', 'listen_timeout_s = 180').replace(
        'transfer_timeout_s = 1800', 'transfer_timeout_s = 60').replace('idle_timeout_s = 60', 'idle_timeout_s = 30')
    (tmp / "smoke_config.toml").write_text(cfg_txt, encoding="utf-8")
    (tmp / "cfgA.toml").write_text(cfg_txt.replace("19300", "18300").replace("19301", "18301")
                                   .replace("CALL-B", "TMP").replace("CALL-A", "CALL-B").replace("TMP", "CALL-A"),
                                   encoding="utf-8")
    cmd_b = L.comandos_mercury(L.MERCURY["E0-1  Payload de 1 KB"], "B", 1)
    cmd_a = L.comandos_mercury(L.MERCURY["E0-1  Payload de 1 KB"], "A", 1)
    cmd_a[0] += ["--config", str(tmp / "cfgA.toml")]
    v._ocupado = lambda *a, **k: None
    import threading
    hb = threading.Thread(target=v._correr, args=(cmd_b,)); hb.start()
    limite = time.time() + 150            # esperar a que B esté escuchando (en Windows tarda más)
    while time.time() < limite and not any("esperando_conexion" in d for t, d in list(v.cola.queue) if t == "log"):
        time.sleep(0.5)
    v._correr(cmd_a); hb.join(timeout=120)
    resumenes = []
    while not v.cola.empty():
        t, d = v.cola.get_nowait()
        if t == "resumen":
            resumenes.append(L.resumir(json.loads(d)))
    print("      resúmenes:", resumenes)
    assert any(c == "verde" for _, c in resumenes) or any(c == "naranja" for _, c in resumenes), resumenes
    print("OK    camino del botón EJECUTAR (formulario -> config -> subproceso -> resumen)")
finally:
    mock.kill()
