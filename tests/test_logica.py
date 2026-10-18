import json, sys, tomllib, subprocess, os, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "smoke"))
import smoke_gui_logica as L

ok = lambda c, m: print(("OK   " if c else "FALLA"), m) or (c or sys.exit(1))

# 1) validación
v = dict(rol="A", mycall=" xq2cg ", peer_call="ea5abc", bw="BW500", pat_url="http://localhost:8080")
ok(L.validar_formulario(v) == [] and v["mycall"] == "XQ2CG", "formulario válido y en mayúsculas")
ok(len(L.validar_formulario(dict(v, peer_call="XQ2CG"))) == 1, "indicativos iguales rechazados")
ok(len(L.validar_formulario(dict(v, mycall="", pat_url="localhost"))) == 2, "campos vacíos / URL sin http rechazados")
ok(len(L.validar_formulario(dict(v, mycall="XQ2'CG;"))) >= 1, "caracteres raros rechazados")

# 2) la configuración generada es TOML válido, incluso con rutas de Windows y sin espacios finales
w = dict(v, mercury_zip=r"C:\Users\Ana Pérez\Downloads\mercury.zip", mercury_exe=r"C:\mercury\mercury.exe", pat_exe="")
cfg = tomllib.loads(L.texto_config(w))
ok(cfg["binaries"]["mercury_zip"] == r"C:\Users\Ana Pérez\Downloads\mercury.zip", "rutas de Windows con barras y tilde intactas")
ok(cfg["mercury"]["mycall"] == "XQ2CG" and cfg["pat"]["peer_call"] == "EA5ABC" and cfg["mercury"]["error_patterns"] == [], "valores del formulario en el TOML")
ok(cfg["mercury"]["cmd_port"] == 8300 and cfg["mercury"]["data_port"] == 8301, "puertos 8300/8301")

# 3) la configuración generada es la que leen los scripts (carga real con load_config)
(Path(tempfile.gettempdir()) / "gui_prueba.toml").write_text(L.texto_config(w), encoding="utf-8")
from smoke_common import load_config
c2 = load_config(str(Path(tempfile.gettempdir()) / "gui_prueba.toml")); ok(c2["mercury"]["bw_command"] == "BW500", "load_config acepta el TOML generado")

# 4) planes de comandos
m = L.comandos_mercury(L.MERCURY["H1  Prueba formal (200 B)"], "B", 2)[0]
ok(m == ["mercury_smoke_client.py", "listen", "--tag", "H1-2", "--phase", "smoke", "--payload-size", "200", "--bw-when", "before"], "H1 rep 2 en PC B = listen --phase smoke")
m = L.comandos_mercury(L.MERCURY["E0-2  Variante V2 (BW después de conectar)"], "A", 1)[0]
ok("send" in m and m[-1] == "after", "E0-2 V2 en A = send --bw-when after")
a = L.comandos_pat(L.PAT["E0-3  Mensaje con adjunto de 200 B"], "A", 1, "EA5ABC", None, None)
ok([x[1] for x in a] == ["queue", "connect"], "E0-3 en A: queue, connect (sin disconnect)")
a = L.comandos_pat(L.PAT["H2  Prueba formal (200 B)"], "A", 3, "EA5ABC", None, None)
ok([x[1] for x in a] == ["disconnect", "queue", "connect"] and "H2-3" in a[1], "H2 en A: disconnect, queue, connect")
try: L.comandos_pat(L.PAT["E0-3  Mensaje con adjunto de 200 B"], "B", 1, "X", None, None); ok(False, "B sin state debe avisar")
except ValueError as e: ok("state.json" in str(e), "E0-3 en B sin state.json avisa con mensaje claro")
try: L.comandos_pat(L.PAT["E0-4  ACK en sesión de retorno"], "B", 1, "X", None, {"resultado": "fallo"}); ok(False, "E0-4 sin E0-3 aprobado")
except ValueError as e: ok("E0-3" in str(e), "E0-4 en B exige E0-3 aprobado")
b = L.comandos_pat(L.PAT["E0-4  ACK en sesión de retorno"], "B", 1, "XQ2CG", None, {"resultado": "aprobado", "run_uuid": "U-1", "sha256_recibido": "ab"*32})
ok(b[0][1] == "ack-queue" and "U-1" in b[0] and b[1][1] == "connect", "E0-4 en B: ack-queue con UUID y SHA-256, luego connect")

# 5) lectura de líneas de salida y resumen
ok(L.ruta_resultado_en_linea("Resultado guardado en: results/e0/X/result.json") == "results/e0/X/result.json", "ruta de result.json (Mercury)")
ok(L.ruta_resultado_en_linea("Resultado: ok_http  ->  C:\\s\\results\\e0\\Y\\result.json") == "C:\\s\\results\\e0\\Y\\result.json", "ruta de result.json (Pat, Windows)")
ok(L.ruta_state_en_linea("state.json (copiar al PC B para check-in): /r/state.json") == "/r/state.json", "ruta de state.json")
ok(L.resumir({"modo": "send", "completado_emisor": True})[1] == "naranja", "emisor OK = naranja (falta cruzar), nunca verde")
ok(L.resumir({"modo": "listen", "entregado_integro": True})[1] == "verde", "receptor con hash = verde")
ok(L.resumir({"modo": "listen", "entregado_integro": False, "fallos": ["x"]})[1] == "rojo", "receptor con fallo = rojo")
ok(L.resumir({"paso": "connect", "sesion": "ok_http"})[1] == "naranja", "HTTP 200 de connect = naranja")
ok(L.resumir({"avisos": [], "resultado": "aprobado"})[1] == "verde", "cruce aprobado = verde")
print("TODO OK")

# 6) estados actuales de los scripts (revisión del lanzador)
ok(L.resumir({"paso": "queue", "resultado": "ok_local"})[1] == "naranja", "queue ok_local = naranja (solo local)")
ok(L.resumir({"modo": "listen", "entregado_integro": True, "evidencia_incompleta": ["x"]})[1] == "naranja",
   "receptor íntegro pero con evidencia incompleta = naranja, no verde")
ok(L.resumir({"hash_coincide_emisor_receptor": True, "resultado": "incompleto", "avisos": ["a"]})[1] == "naranja",
   "cruce incompleto = naranja")
ok(L.resumir({"hash_coincide_emisor_receptor": False, "resultado": "fallo", "avisos": ["a"]})[1] == "rojo",
   "cruce fallo = rojo")
ok(L.resumir({"paso": "check-in", "resultado": "aprobado"})[1] == "verde", "check-in aprobado = verde")
ok(L.resumir({"paso": "check-in", "resultado": "aprobado", "evidencia_incompleta": ["y"]})[1] == "naranja",
   "check-in aprobado con evidencia incompleta = naranja")
cfg2 = tomllib.loads(L.texto_config(dict(w, mercury_ini=r"C:\Mercury\mercury.ini", pat_log="")))
ok(cfg2["registrar"]["mercury_ini"] == r"C:\Mercury\mercury.ini" and cfg2["registrar"]["pat_log"] == "",
   "sección [registrar] generada")
print("TODO OK (lanzador)")

