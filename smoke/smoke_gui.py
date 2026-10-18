"""smoke_gui.pyw - ventana con botones para ejecutar el smoke test sin escribir comandos.

CÓMO SE USA: doble clic en HF-ARQ-Smoke.exe (o, con Python instalado, en Iniciar.bat).
Rellenar «1. Este PC», elegir la prueba en «2. Prueba» y pulsar EJECUTAR. El resultado
aparece en grande. Los resultados quedan en la carpeta HF-ARQ-Smoke-datos, junto al .exe.

QUÉ HACE
  - Guarda lo escrito en el formulario y genera smoke_config.toml.
  - Ejecuta los mismos scripts que se usarían a mano (mercury_smoke_client.py,
    pat_smoke_test.py, smoke_cross_check.py) y muestra su registro en vivo.
  - Muestra el resultado con color: verde (aprobado), rojo (fallo) o naranja (a revisar).
  - Abre la carpeta de resultados y la comprime en un .zip para llevarla a otro PC.

QUÉ NO HACE
  - No inicia Mercury ni Pat, ni cambia su configuración (eso se hace a mano y se registra).
  - No decide si algo aprobó: muestra lo que decidieron los scripts.
  - No copia archivos entre PCs por su cuenta: el state.json del PC A se lleva al PC B
    con el botón «Guardar copia de state.json» y «Elegir state.json» (pendrive o carpeta compartida).
  - No reintenta pruebas ni encadena variantes: cada pulsación es una prueba.

POR QUÉ
  - El usuario solo dispone de mouse y teclado mínimo: la ventana evita escribir comandos y
    reduce errores de tipeo, pero ejecuta exactamente lo mismo que la línea de comandos.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import smoke_gui_logica as L

# Modo «ejecutar un script»: el .exe se relanza a sí mismo con --script (ver smoke_gui_logica).
# Se decide ANTES de importar tkinter para que este modo no necesite ventana.
if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--script":
    sys.exit(L.ejecutar_script(sys.argv[2], sys.argv[3:]))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Importados aquí para que PyInstaller incluya los scripts dentro del .exe.
import mercury_smoke_client  # noqa: F401
import pat_smoke_test  # noqa: F401
import smoke_cross_check  # noqa: F401

EMPAQUETADO = bool(getattr(sys, "frozen", False))


def carpeta_de_trabajo() -> Path:
    """Dónde se guardan configuración y resultados. Con el .exe: HF-ARQ-Smoke-datos junto al
    programa (o en Documentos si esa carpeta no admite escritura). Sin .exe: junto al script."""
    if not EMPAQUETADO:
        return Path(__file__).resolve().parent
    candidatas = [Path(sys.executable).resolve().parent / "HF-ARQ-Smoke-datos",
                  Path.home() / "Documents" / "HF-ARQ-Smoke-datos"]
    for c in candidatas:
        try:
            c.mkdir(parents=True, exist_ok=True)
            prueba = c / ".escritura"
            prueba.write_text("ok", encoding="utf-8")
            prueba.unlink()
            return c
        except OSError:
            continue
    return candidatas[-1]


AQUI = carpeta_de_trabajo()
RESULTADOS = AQUI / "results"
AJUSTES = AQUI / "smoke_gui_ajustes.json"
COLORES = {"verde": "#1b7f3b", "rojo": "#b3261e", "naranja": "#b26a00", "gris": "#555555"}


def comando_de_proceso(cmd: list) -> list:
    """Lista de argumentos para lanzar un script. .exe: se relanza a sí mismo con --script;
    con Python: se ejecuta el script con python.exe (sin consola propia del programa)."""
    if EMPAQUETADO:
        return [sys.executable, "--script", *cmd]
    return [python_consola(), "-u", str(Path(__file__).resolve().parent / cmd[0]), *cmd[1:]]


def python_consola() -> str:
    """Usa python.exe (no pythonw.exe) para los scripts, así tienen salida estándar."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        alt = exe.with_name("python.exe")
        if alt.exists():
            return str(alt)
    return str(exe)


class Ventana(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Smoke test Mercury + Pat")
        self.geometry("980x860")
        self.cola: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.proc = None
        self.hilo = None
        self.state_elegido = tk.StringVar()
        self._ultimo_resultado: str | None = None
        self.v = {k: tk.StringVar() for k in
                  ("rol", "mycall", "peer_call", "bw", "pat_url", "mercury_zip", "mercury_exe", "pat_exe",
                   "mercury_ini", "mercury_log", "pat_config", "pat_log")}
        self.v["rol"].set("A")
        self.v["bw"].set("BW500")
        self.v["pat_url"].set("http://localhost:8080")
        self._cargar_ajustes()
        self._construir()
        self.after(100, self._vaciar_cola)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)

    # ------------------------------------------------------------ interfaz
    def _construir(self):
        pad = dict(padx=8, pady=4)
        f1 = ttk.LabelFrame(self, text="1. Este PC")
        f1.pack(fill="x", **pad)
        ttk.Label(f1, text="Este PC es:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Radiobutton(f1, text="PC A (emisor)", variable=self.v["rol"], value="A").grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(f1, text="PC B (receptor)", variable=self.v["rol"], value="B").grid(row=0, column=2, sticky="w")
        ttk.Label(f1, text="Mi indicativo:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(f1, textvariable=self.v["mycall"], width=16).grid(row=1, column=1, sticky="w")
        ttk.Label(f1, text="Indicativo del otro PC:").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(f1, textvariable=self.v["peer_call"], width=16).grid(row=1, column=3, sticky="w")
        ttk.Label(f1, text="Ancho de banda:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(f1, textvariable=self.v["bw"], values=L.ANCHOS, width=10,
                     state="readonly").grid(row=2, column=1, sticky="w")
        ttk.Label(f1, text="Dirección de Pat:").grid(row=2, column=2, sticky="e", **pad)
        ttk.Entry(f1, textvariable=self.v["pat_url"], width=26).grid(row=2, column=3, sticky="w")
        for i, (clave, texto) in enumerate((("mercury_zip", "Mercury .zip (para registrar su SHA-256):"),
                                            ("mercury_exe", "mercury.exe:"), ("pat_exe", "pat.exe:"),
                                            ("mercury_ini", "Configuración de Mercury (.ini):"),
                                            ("mercury_log", "Registro (log) de Mercury:"),
                                            ("pat_config", "Configuración de Pat (config.json):"),
                                            ("pat_log", "Registro (log) de Pat:"))):
            ttk.Label(f1, text=texto).grid(row=3 + i, column=0, columnspan=2, sticky="w", **pad)
            ttk.Entry(f1, textvariable=self.v[clave], width=60).grid(row=3 + i, column=2, columnspan=2, sticky="we")
            ttk.Button(f1, text="Examinar…", command=lambda c=clave: self._examinar(c)).grid(row=3 + i, column=4, **pad)

        f2 = ttk.LabelFrame(self, text="2. Prueba")
        f2.pack(fill="x", **pad)
        self.grupo = tk.StringVar(value="Mercury (puertos 8300/8301)")
        ttk.Label(f2, text="Programa:").grid(row=0, column=0, sticky="w", **pad)
        cg = ttk.Combobox(f2, textvariable=self.grupo, state="readonly", width=30,
                          values=["Mercury (puertos 8300/8301)", "Pat (API HTTP)"])
        cg.grid(row=0, column=1, sticky="w")
        cg.bind("<<ComboboxSelected>>", lambda e: self._llenar_pruebas())
        ttk.Label(f2, text="Prueba:").grid(row=1, column=0, sticky="w", **pad)
        self.prueba = tk.StringVar()
        self.cb_prueba = ttk.Combobox(f2, textvariable=self.prueba, state="readonly", width=62)
        self.cb_prueba.grid(row=1, column=1, columnspan=3, sticky="w")
        ttk.Label(f2, text="Repetición (solo H1/H2):").grid(row=2, column=0, sticky="w", **pad)
        self.rep = tk.IntVar(value=1)
        ttk.Spinbox(f2, from_=1, to=3, textvariable=self.rep, width=4).grid(row=2, column=1, sticky="w")
        self.btn_ejecutar = tk.Button(f2, text="EJECUTAR", font=("Segoe UI", 13, "bold"), bg="#1f5fbf",
                                      fg="white", command=self._ejecutar, padx=24, pady=6)
        self.btn_ejecutar.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        self.btn_detener = ttk.Button(f2, text="Detener", command=self._detener, state="disabled")
        self.btn_detener.grid(row=3, column=2, sticky="w", **pad)
        self._llenar_pruebas()

        f3 = ttk.LabelFrame(self, text="3. Resultado")
        f3.pack(fill="both", expand=True, **pad)
        self.lbl = tk.Label(f3, text="Sin ejecutar.", font=("Segoe UI", 12, "bold"), fg=COLORES["gris"],
                            wraplength=900, justify="left", anchor="w")
        self.lbl.pack(fill="x", padx=8, pady=6)
        marco = ttk.Frame(f3)
        marco.pack(fill="both", expand=True, padx=8, pady=4)
        self.txt = tk.Text(marco, height=14, wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(marco, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        f4 = ttk.LabelFrame(self, text="Herramientas")
        f4.pack(fill="x", **pad)
        botones = [("Abrir carpeta de resultados", self._abrir_resultados),
                   ("Comprimir resultados (.zip)", self._comprimir),
                   ("Cruzar resultados A y B…", self._cruzar),
                   ("Elegir state.json (PC B)…", self._elegir_state),
                   ("Guardar copia de state.json (PC A)…", self._guardar_state)]
        for i, (t, c) in enumerate(botones):
            ttk.Button(f4, text=t, command=c).grid(row=0, column=i, padx=4, pady=6)
        ttk.Label(f4, textvariable=self.state_elegido, foreground="#555").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=8)
        ttk.Label(f4, text=f"Carpeta de trabajo (config y resultados): {AQUI}",
                  foreground="#555").grid(row=2, column=0, columnspan=5, sticky="w", padx=8)

    def _llenar_pruebas(self):
        cat = L.MERCURY if self.grupo.get().startswith("Mercury") else L.PAT
        self.cb_prueba["values"] = list(cat)
        self.prueba.set(next(iter(cat)))

    # ------------------------------------------------------------ utilidades
    def _examinar(self, clave):
        ruta = filedialog.askopenfilename(title="Elegir archivo")
        if ruta:
            self.v[clave].set(ruta)

    def _escribir(self, texto, tag=None):
        self.txt.insert("end", texto + "\n", tag)
        self.txt.see("end")

    def _mostrar(self, frase, color):
        self.lbl.configure(text=frase, fg=COLORES.get(color, COLORES["gris"]))

    def _datos(self) -> dict:
        return {k: var.get() for k, var in self.v.items()}

    def _cargar_ajustes(self):
        try:
            for k, val in json.loads(AJUSTES.read_text(encoding="utf-8")).items():
                if k in self.v:
                    self.v[k].set(val)
        except (OSError, ValueError):
            pass

    def _guardar_ajustes(self):
        try:
            AJUSTES.write_text(json.dumps(self._datos(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ ejecución
    def _ocupado(self, si: bool):
        self.btn_ejecutar.configure(state="disabled" if si else "normal")
        self.btn_detener.configure(state="normal" if si else "disabled")

    def _ejecutar(self):
        if self.hilo and self.hilo.is_alive():
            return
        datos = self._datos()
        errores = L.validar_formulario(datos)
        if errores:
            messagebox.showwarning("Faltan datos", "\n".join(errores))
            return
        self.v["mycall"].set(datos["mycall"])
        self.v["peer_call"].set(datos["peer_call"])
        self._guardar_ajustes()
        (AQUI / "smoke_config.toml").write_text(L.texto_config(datos), encoding="utf-8")
        try:
            comandos = self._plan(datos)
        except ValueError as e:
            messagebox.showwarning("No se puede ejecutar todavía", str(e))
            return
        self.txt.delete("1.0", "end")
        self._mostrar("Ejecutando… (no cierre la ventana)", "gris")
        if self.prueba.get().startswith("E0-2  Variante V3"):
            self._escribir(L.INSTRUCCION_V3)
        self._ocupado(True)
        self.hilo = threading.Thread(target=self._correr, args=(comandos,), daemon=True)
        self.hilo.start()

    def _plan(self, datos) -> list:
        rol, rep, nombre = datos["rol"], self.rep.get(), self.prueba.get()
        if self.grupo.get().startswith("Mercury"):
            return L.comandos_mercury(L.MERCURY[nombre], rol, rep)
        state = self.state_elegido.get() or None
        if state is None:
            ult = L.ultimo_state(RESULTADOS)
            state = str(ult) if ult else None
        return L.comandos_pat(L.PAT[nombre], rol, rep, datos["peer_call"], state,
                              L.ultimo_check_in(RESULTADOS))

    def _correr(self, comandos):
        """Hilo de trabajo: ejecuta cada comando en orden y envía su salida a la cola."""
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ultimo_res = None
        for cmd in comandos:
            self.cola.put(("log", f"\n>>> {' '.join(cmd)}"))
            try:
                self.proc = subprocess.Popen(comando_de_proceso(cmd), cwd=str(AQUI), env=env,
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                             encoding="utf-8", errors="replace", creationflags=flags)
            except OSError as e:
                self.cola.put(("log", f"No se pudo iniciar el script: {e}"))
                self.cola.put(("resumen", json.dumps({"excepcion": str(e)})))
                break
            for linea in self.proc.stdout:
                linea = linea.rstrip("\n")
                self.cola.put(("log", linea))
                ruta = L.ruta_resultado_en_linea(linea)
                if ruta:
                    ultimo_res = ruta
                st = L.ruta_state_en_linea(linea)
                if st:
                    self.cola.put(("state", st))
            codigo = self.proc.wait()
            # Los scripts imprimen la ruta relativa a su carpeta de trabajo (AQUI), no a la del programa.
            ruta_res = (AQUI / ultimo_res) if ultimo_res else None
            if ruta_res and ruta_res.is_file():
                self.cola.put(("resumen", ruta_res.read_text(encoding="utf-8")))
            # Un paso que falla corta la secuencia (salvo «disconnect», que es solo preparación).
            if codigo != 0 and "disconnect" not in cmd:
                break
        self.cola.put(("fin", ""))

    def _vaciar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "log":
                    self._escribir(dato)
                elif tipo == "state":
                    self.state_elegido.set(dato)
                    self._escribir("Archivo state.json listo. Cópielo al PC B con «Guardar copia de state.json».")
                elif tipo == "resumen":
                    try:
                        frase, color = L.resumir(json.loads(dato))
                    except ValueError:
                        frase, color = "No se pudo leer el resultado.", "rojo"
                    self._mostrar(frase, color)
                elif tipo == "fin":
                    self._ocupado(False)
        except queue.Empty:
            pass
        self.after(100, self._vaciar_cola)

    def _detener(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._escribir("Detenido por el usuario.")

    # ------------------------------------------------------------ herramientas
    def _abrir_resultados(self):
        RESULTADOS.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(str(RESULTADOS))  # noqa: S606
        else:
            messagebox.showinfo("Resultados", str(RESULTADOS))

    def _comprimir(self):
        if not RESULTADOS.exists():
            messagebox.showinfo("Resultados", "Todavía no hay resultados.")
            return
        destino = AQUI / f"resultados_{self.v['rol'].get()}_{time.strftime('%Y%m%d_%H%M%S')}"
        archivo = shutil.make_archive(str(destino), "zip", str(RESULTADOS))
        messagebox.showinfo("Resultados comprimidos", f"Archivo creado:\n{archivo}")

    def _elegir_state(self):
        ruta = filedialog.askopenfilename(title="Elegir el state.json del PC A",
                                          filetypes=[("state.json", "state.json"), ("JSON", "*.json")])
        if ruta:
            self.state_elegido.set(ruta)

    def _guardar_state(self):
        origen = self.state_elegido.get() or (str(L.ultimo_state(RESULTADOS) or ""))
        if not origen or not Path(origen).is_file():
            messagebox.showinfo("state.json", "Todavía no hay un state.json. Ejecute primero E0-3 o H2 en este PC.")
            return
        destino = filedialog.asksaveasfilename(title="Guardar copia de state.json", initialfile="state.json",
                                               defaultextension=".json")
        if destino:
            shutil.copyfile(origen, destino)
            messagebox.showinfo("state.json", f"Copia guardada en:\n{destino}")

    def _cruzar(self):
        em = filedialog.askopenfilename(title="1/2: result.json del EMISOR (PC A)", initialdir=str(RESULTADOS),
                                        filetypes=[("result.json", "result.json")])
        if not em:
            return
        rx = filedialog.askopenfilename(title="2/2: result.json del RECEPTOR (PC B)", initialdir=str(RESULTADOS),
                                        filetypes=[("result.json", "result.json")])
        if not rx or (self.hilo and self.hilo.is_alive()):
            return
        self.txt.delete("1.0", "end")
        self._ocupado(True)
        self.hilo = threading.Thread(target=self._correr_cruce, args=(em, rx), daemon=True)
        self.hilo.start()

    def _correr_cruce(self, em, rx):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        r = subprocess.run(comando_de_proceso(["smoke_cross_check.py", em, rx]), cwd=str(AQUI), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=flags)
        self.cola.put(("log", r.stdout + r.stderr))
        cruce = Path(rx).with_name("cruce.json")
        if cruce.is_file():
            self.cola.put(("resumen", cruce.read_text(encoding="utf-8")))
        self.cola.put(("fin", ""))

    def _cerrar(self):
        if self.hilo and self.hilo.is_alive():
            if not messagebox.askyesno("Hay una prueba en curso", "¿Cerrar y detenerla?"):
                return
            self._detener()
        self.destroy()


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("Python demasiado antiguo",
                             "Se necesita Python 3.11 o superior (python.org).")
        sys.exit(1)
    Ventana().mainloop()
