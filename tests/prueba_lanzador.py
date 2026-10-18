"""Prueba de extremo a extremo del lanzador (y del .exe) contra un Mercury SIMULADO.

QUÉ HACE
  - Arranca tests/mock_mercury.py (NO es Mercury) y ejecuta, a través del lanzador indicado,
    un envío y una recepción completos más el cruce de resultados.
  - Comprueba que el lanzador captura la salida de los scripts y que el payload llega íntegro.
  - Sirve igual con «python smoke/smoke_gui.py» que con el HF-ARQ-Smoke.exe empaquetado.

QUÉ NO HACE
  - No prueba Mercury, Pat ni radio: valida solo el empaquetado y la lógica.
  - No exige código de salida 0: en un equipo sin los comandos de captura de Windows la
    evidencia puede salir «incompleta» y eso no es un fallo del empaquetado.

USO
  python tests/prueba_lanzador.py --launcher "python smoke/smoke_gui.py"
  python tests/prueba_lanzador.py --launcher "dist/HF-ARQ-Smoke.exe"
"""
import argparse
import glob
import json
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent

CONFIG = """[general]
results_dir = '{res}'
payload_seed = 20261018
max_payload_bytes = 1048576
[mercury]
host = "127.0.0.1"
cmd_port = {cp}
data_port = {dp}
mycall = "CALL-{yo}"
peer_call = "CALL-{otro}"
bw_command = "BW500"
cmd_response_window_s = 0.3
connect_timeout_s = 90
listen_timeout_s = 180
transfer_timeout_s = 60
idle_timeout_s = 30
extra_bytes_grace_s = 1
disconnect_wait_s = 30
session_tolerance_ms = 50
error_patterns = []
[pat]
base_url = "http://localhost:8080"
mycall = "CALL-{yo}"
peer_call = "CALL-{otro}"
http_timeout_s = 30
connect_timeout_s = 1800
transport = "varahf"
[binaries]
mercury_zip = ''
mercury_exe = ''
pat_exe = ''
[registrar]
mercury_ini = ''
mercury_log = ''
pat_config = ''
pat_log = ''
"""


def ok(cond: bool, texto: str) -> None:
    print(("OK    " if cond else "FALLA ") + texto, flush=True)
    if not cond:
        sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--launcher", required=True, help='Ej.: "python smoke/smoke_gui.py"')
    a = ap.parse_args()
    lanz = shlex.split(a.launcher, posix=(sys.platform != "win32")) + ["--script"]
    tmp = Path(tempfile.mkdtemp(prefix="hfarq_"))
    cfg = {}
    for yo, otro, cp, dp in (("A", "B", 18300, 18301), ("B", "A", 19300, 19301)):
        (tmp / f"res{yo}").mkdir()
        ruta = tmp / f"cfg{yo}.toml"
        ruta.write_text(CONFIG.format(res=str(tmp / f"res{yo}"), cp=cp, dp=dp, yo=yo, otro=otro),
                        encoding="utf-8")
        cfg[yo] = ruta

    # 1) el lanzador captura la salida (el script imprime su ayuda y devuelve 2)
    r = subprocess.run(lanz + ["smoke_cross_check.py"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    ok(r.returncode == 2 and len(r.stdout) > 20, "el lanzador devuelve la salida y el código del script")
    r = subprocess.run(lanz + ["script_inexistente.py"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    ok(r.returncode == 2 and "desconocido" in r.stdout, "script desconocido rechazado con código 2")

    # 2) envío y recepción completos contra el Mercury simulado
    mock = subprocess.Popen([sys.executable, str(AQUI / "mock_mercury.py"), "ok"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        base = ["mercury_smoke_client.py"]
        rx = subprocess.Popen(lanz + base + ["listen", "--config", str(cfg["B"]), "--tag", "T",
                                             "--payload-size", "1024"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace")
        # Espera a que el receptor esté realmente escuchando (en Windows el arranque tarda más).
        lineas_rx: list[str] = []
        lector = threading.Thread(target=lambda: [lineas_rx.append(l) for l in rx.stdout], daemon=True)
        lector.start()
        limite = time.time() + 150
        while time.time() < limite and not any("esperando_conexion" in l for l in lineas_rx):
            time.sleep(0.5)
        ok(any("esperando_conexion" in l for l in lineas_rx), "el receptor queda escuchando")
        tx = subprocess.run(lanz + base + ["send", "--config", str(cfg["A"]), "--tag", "T",
                                           "--payload-size", "1024"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        rx.wait(timeout=180); lector.join(timeout=10); salida_rx = ''.join(lineas_rx)
        ok("Resultado guardado en:" in tx.stdout, "la salida del emisor llega al lanzador")
        ok("Resultado guardado en:" in salida_rx, "la salida del receptor llega al lanzador")
        res_a = json.loads(Path(glob.glob(str(tmp / "resA" / "e0" / "*" / "result.json"))[0]).read_text("utf-8"))
        res_b = json.loads(Path(glob.glob(str(tmp / "resB" / "e0" / "*" / "result.json"))[0]).read_text("utf-8"))
        ok(res_a.get("completado_emisor") is True, "emisor completado")
        ok(res_b.get("entregado_integro") is True, "receptor: payload íntegro (hash verificado)")
        cruce = subprocess.run(lanz + ["smoke_cross_check.py",
                                       glob.glob(str(tmp / "resA" / "e0" / "*" / "result.json"))[0],
                                       glob.glob(str(tmp / "resB" / "e0" / "*" / "result.json"))[0]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        veredicto = json.loads(cruce.stdout)["resultado"]
        ok(veredicto in ("aprobado", "incompleto"), f"cruce A/B: {veredicto}")
    finally:
        mock.kill()
    print("TODO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
