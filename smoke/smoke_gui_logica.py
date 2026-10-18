"""smoke_gui_logica.py - lógica de la ventana del smoke test, SIN interfaz gráfica.

La ventana (smoke_gui.pyw) solo dibuja botones; todo lo que se decide está aquí para
poder probarlo sin pantalla.

QUÉ HACE
  - Valida los datos del formulario y genera smoke_config.toml (el usuario no edita TOML).
  - Traduce cada prueba elegida (E0-1, E0-2, E0-3, E0-4, H1, H2) y el rol del PC (A o B) en
    la lista exacta de comandos de los scripts, con los mismos argumentos que se
    escribirían a mano.
  - Busca el último state.json / check-in de la carpeta de resultados.
  - Resume un result.json en una frase y un color (aprobado / fallo / a revisar).

QUÉ NO HACE
  - No decide si una prueba aprobó: lee lo que decidieron los scripts.
  - No ejecuta nada, no abre ventanas ni toca Mercury o Pat.
  - No cambia los criterios del smoke test v6.

POR QUÉ
  - Separar la lógica de la ventana permite probarla sin interfaz y garantiza que el
    botón haga exactamente lo mismo que el comando equivalente.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ----------------------------------------------------------------- validación
RX_INDICATIVO = re.compile(r"^[A-Z0-9/-]{3,12}$")
ANCHOS = ["BW500", "BW2300"]


def validar_formulario(v: dict) -> list[str]:
    """Devuelve la lista de problemas (vacía si todo está bien)."""
    errores = []
    for campo, nombre in (("mycall", "Mi indicativo"), ("peer_call", "Indicativo del otro PC")):
        v[campo] = v.get(campo, "").strip().upper()
        if not RX_INDICATIVO.match(v[campo]):
            errores.append(f"{nombre}: use letras, números, / o - (3 a 12 caracteres).")
    if not errores and v["mycall"] == v["peer_call"]:
        errores.append("Los dos indicativos deben ser distintos (cada PC tiene el suyo).")
    if v.get("bw") not in ANCHOS:
        errores.append("Elija el ancho de banda.")
    if not re.match(r"^https?://[^\s']+$", v.get("pat_url", "")):
        errores.append("La dirección de Pat debe empezar con http:// (normalmente http://localhost:8080).")
    if v.get("rol") not in ("A", "B"):
        errores.append("Indique si este PC es A o B.")
    return errores


def _lit(texto: str) -> str:
    """Cadena literal TOML (comillas simples): las barras de Windows no se interpretan."""
    return "'" + (texto or "").replace("'", "") + "'"


def texto_config(v: dict) -> str:
    return f"""# Generado por la ventana. No editar a mano: se reescribe en cada ejecución.
[general]
results_dir = "results"
payload_seed = 20261018
max_payload_bytes = 1048576

[mercury]
host = "127.0.0.1"
cmd_port = 8300
data_port = 8301
mycall = {_lit(v['mycall'])}
peer_call = {_lit(v['peer_call'])}
bw_command = {_lit(v['bw'])}
cmd_response_window_s = 2.0
connect_timeout_s = 180
listen_timeout_s = 900
transfer_timeout_s = 1800
idle_timeout_s = 60
extra_bytes_grace_s = 5
disconnect_wait_s = 60
session_tolerance_ms = 50
error_patterns = []

[pat]
base_url = {_lit(v['pat_url'])}
mycall = {_lit(v['mycall'])}
peer_call = {_lit(v['peer_call'])}
http_timeout_s = 30
connect_timeout_s = 1800
transport = "varahf"

[binaries]
mercury_zip = {_lit(v.get('mercury_zip', ''))}
mercury_exe = {_lit(v.get('mercury_exe', ''))}
pat_exe = {_lit(v.get('pat_exe', ''))}

[registrar]
mercury_ini = {_lit(v.get('mercury_ini', ''))}
mercury_log = {_lit(v.get('mercury_log', ''))}
pat_config = {_lit(v.get('pat_config', ''))}
pat_log = {_lit(v.get('pat_log', ''))}
"""


# --------------------------------------------------------------- catálogo de pruebas
# nombre visible -> parámetros. 'rep' indica que la prueba formal lleva número de repetición.
MERCURY = {
    "E0-1  Payload de 16 B": dict(fase="e0", tag="E0-1_16B", size=16, bw="before"),
    "E0-1  Payload de 1 KB": dict(fase="e0", tag="E0-1_1KB", size=1024, bw="before"),
    "E0-1  Payload de 10 KB": dict(fase="e0", tag="E0-1_10KB", size=10240, bw="before"),
    "E0-2  Variante V1 (BW antes de conectar)": dict(fase="e0", tag="E0-2_V1", size=200, bw="before"),
    "E0-2  Variante V2 (BW después de conectar)": dict(fase="e0", tag="E0-2_V2", size=200, bw="after"),
    "E0-2  Variante V3 (receptor escucha antes del emisor)": dict(fase="e0", tag="E0-2_V3", size=200, bw="before"),
    "H1  Prueba formal (200 B)": dict(fase="smoke", tag="H1-{n}", size=200, bw="before", rep=True),
}
PAT = {
    "E0-3  Mensaje con adjunto de 200 B": dict(fase="e0", tag="E0-3_200B", tipo="adjunto", size=200),
    "E0-3  Mensaje con adjunto de 10 KB": dict(fase="e0", tag="E0-3_10KB", tipo="adjunto", size=10240),
    "E0-4  ACK en sesión de retorno": dict(fase="e0", tag="E0-4", tipo="ack"),
    "H2  Prueba formal (200 B)": dict(fase="smoke", tag="H2-{n}", tipo="adjunto", size=200, rep=True),
}
INSTRUCCION_V3 = ("V3: inicie primero el PC receptor (B) y espere a que diga "
                  "'esperando_conexion' antes de iniciar el emisor (A).")


def comandos_mercury(preset: dict, rol: str, rep: int) -> list[list[str]]:
    modo = "send" if rol == "A" else "listen"
    tag = preset["tag"].replace("{n}", str(rep))
    return [["mercury_smoke_client.py", modo, "--tag", tag, "--phase", preset["fase"],
             "--payload-size", str(preset["size"]), "--bw-when", preset["bw"]]]


def comandos_pat(preset: dict, rol: str, rep: int, peer: str,
                 state_path: str | None, check_in_result: dict | None) -> list[list[str]]:
    """Devuelve los comandos o lanza ValueError con un mensaje para el usuario."""
    tag = preset["tag"].replace("{n}", str(rep))
    fase = preset["fase"]
    base = ["--tag", tag, "--phase", fase]
    if preset["tipo"] == "adjunto":
        if rol == "A":
            pasos = [["pat_smoke_test.py", "queue", *base, "--size", str(preset["size"])],
                     ["pat_smoke_test.py", "connect", *base, "--call", peer]]
            if preset.get("rep"):
                # Solo H2: cada repetición parte desconectada (v6, H2 paso 6).
                pasos.insert(0, ["pat_smoke_test.py", "disconnect", *base])
            return pasos
        if not state_path or not Path(state_path).is_file():
            raise ValueError("Falta el archivo state.json que creó el PC A. "
                             "Cópielo a este PC y elíjalo con el botón «Elegir state.json».")
        return [["pat_smoke_test.py", "check-in", *base, "--state", state_path]]
    # ACK de retorno (E0-4)
    if rol == "B":
        if not check_in_result or check_in_result.get("resultado") != "aprobado" \
                or not check_in_result.get("sha256_recibido"):
            raise ValueError("Antes debe existir un E0-3 aprobado en este PC (paso «check-in»).")
        return [["pat_smoke_test.py", "ack-queue", *base, "--to", peer,
                 "--uuid", check_in_result["run_uuid"], "--sha256", check_in_result["sha256_recibido"]],
                ["pat_smoke_test.py", "connect", *base, "--call", peer]]
    if not state_path or not Path(state_path).is_file():
        raise ValueError("No se encontró el state.json del envío original en este PC (PC A).")
    return [["pat_smoke_test.py", "ack-verify", *base, "--state", state_path]]


# --------------------------------------------------------------- resultados
def ultimo_archivo(base: Path, patron: str) -> Path | None:
    candidatos = list(base.glob(patron))
    return max(candidatos, key=lambda p: p.stat().st_mtime) if candidatos else None


def ultimo_state(carpeta_resultados: Path) -> Path | None:
    return ultimo_archivo(carpeta_resultados, "*/*_queue_*/state.json")


def ultimo_check_in(carpeta_resultados: Path) -> dict | None:
    p = ultimo_archivo(carpeta_resultados, "*/*_check-in_*/result.json")
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def resumir(res: dict) -> tuple[str, str]:
    """(frase, color) con color en {'verde','rojo','naranja'}.

    Solo traduce lo que decidieron los scripts. Una entrega verificada con evidencia
    incompleta NUNCA sale en verde (v6 exige guardar esa evidencia para aprobar)."""
    fallos = "; ".join(res.get("fallos") or [])
    if res.get("excepcion"):
        return "ERROR del script. Revise el registro.", "rojo"
    inc = res.get("evidencia_incompleta") or []
    nota_inc = (" EVIDENCIA INCOMPLETA (v6 la exige): " + "; ".join(str(x) for x in inc)) if inc else ""
    modo = res.get("modo")
    if modo == "send":
        if res.get("completado_emisor"):
            return ("EMISOR sin fallos. El enlace confirmó (BUFFER 0), pero la entrega solo la prueba "
                    "el receptor: falta cruzar con el PC B." + nota_inc), "naranja"
        return f"EMISOR con FALLO: {fallos}", "rojo"
    if modo == "listen":
        if res.get("entregado_integro"):
            if inc:
                return "RECEPTOR: payload recibido y verificado (hash correcto)." + nota_inc, "naranja"
            return "RECEPTOR: payload recibido y verificado (hash correcto).", "verde"
        return f"RECEPTOR con FALLO: {fallos}", "rojo"
    if "hash_coincide_emisor_receptor" in res or "cruce" in res:
        r = res.get("resultado")
        if r == "aprobado":
            return "CRUCE aprobado: entrega íntegra verificada y evidencia completa.", "verde"
        if r == "incompleto":
            return ("CRUCE INCOMPLETO: la entrega se verificó, pero falta evidencia que v6 exige. "
                    + "; ".join(res.get("avisos") or [])), "naranja"
        return "CRUCE con FALLO: " + "; ".join(res.get("avisos") or []), "rojo"
    r = res.get("resultado") or res.get("sesion")
    paso = res.get("paso")
    if r == "ok_local":
        return (f"Paso «{paso}» correcto, solo LOCAL (mensaje en cola de Pat; no prueba entrega)."
                + nota_inc), "naranja"
    if r == "ok_http":
        return (f"Paso «{paso}»: la sesión respondió HTTP 200, lo que no prueba entrega; "
                "falta verificar en el otro PC." + nota_inc), "naranja"
    if r == "aprobado":
        return (f"Paso «{paso}» aprobado." + nota_inc), ("naranja" if inc else "verde")
    if r == "no_determinado":
        return f"Paso «{paso}»: no determinado. {res.get('motivo', '')}", "naranja"
    if paso == "disconnect":
        return "Desconexión solicitada.", "naranja"
    return f"Paso «{paso}» con FALLO. {res.get('motivo', '')}", "rojo"


RX_RESULTADO = re.compile(r"(?:Resultado guardado en:|->)\s*(.+result\.json)\s*$")
RX_STATE = re.compile(r"state\.json \(copiar al PC B para check-in\):\s*(.+)$")


def ruta_resultado_en_linea(linea: str) -> str | None:
    m = RX_RESULTADO.search(linea.strip())
    return m.group(1).strip() if m else None


def ruta_state_en_linea(linea: str) -> str | None:
    m = RX_STATE.search(linea.strip())
    return m.group(1).strip() if m else None


# ------------------------------------------------------- ejecución dentro del .exe
# En el .exe no hay «python»: el propio programa se vuelve a lanzar con «--script NOMBRE ...»
# y ejecuta el script pedido. Así el botón hace exactamente lo mismo que el comando equivalente.
SCRIPTS = {"mercury_smoke_client.py": "mercury_smoke_client",
           "pat_smoke_test.py": "pat_smoke_test",
           "smoke_cross_check.py": "smoke_cross_check"}


def ejecutar_script(nombre: str, args: list[str]) -> int:
    import importlib
    import sys
    if nombre not in SCRIPTS:
        print(f"Script desconocido: {nombre}")
        return 2
    for flujo in ("stdout", "stderr"):
        f = getattr(sys, flujo, None)
        if f is None:                       # .exe sin consola y sin tubería heredada
            import os
            setattr(sys, flujo, open(os.devnull, "w", encoding="utf-8"))
        else:
            try:
                f.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    mod = importlib.import_module(SCRIPTS[nombre])
    sys.argv = [nombre, *args]
    try:
        r = mod.main()
    except SystemExit as e:                 # argparse usa sys.exit
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
    return int(r or 0)
