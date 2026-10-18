# Scripts del smoke test (Mercury 1.9.19 y Pat 1.0.0, Windows)

Requisitos: Python 3.11+, sin paquetes externos. Copiar esta carpeta a los DOS PCs y
copiar `smoke_config.example.toml` a `smoke_config.toml` en cada uno (cada PC con su
indicativo en `mycall` y el del otro en `peer_call`).

| Archivo | Cubre | Se ejecuta en |
|---|---|---|
| `mercury_smoke_client.py` | E0-1, E0-2, H1 (puertos 8300/8301) | `listen` en B, `send` en A |
| `smoke_cross_check.py` | une el resultado de A y de B | cualquier PC, con ambos result.json |
| `pat_smoke_test.py` | E0-3, E0-4, H2 (API HTTP de Pat) | por subcomando, en el PC que corresponda |
| `smoke_common.py` | registro, hashes, captura TCP | (librería) |

Cada ejecución crea `results/<e0|smoke>/<run_id>/` con `events.jsonl`, bytes crudos de
los sockets, `environment.json`, la configuración usada y `result.json`.
Las instrucciones de uso y el QUÉ HACE / QUÉ NO HACE / POR QUÉ están al inicio de cada script.

## Qué se probó y qué no
- Se probó la lógica contra un Mercury y un Pat SIMULADOS escritos a partir de la
  descripción del documento (no son los programas reales): entrega correcta, payload
  truncado (timeout), bytes extra, adjunto corrupto, HTTP 500 y ACK de retorno.
- NO se probó contra Mercury ni Pat reales. Siguen sin confirmar: el formato de las
  respuestas de Mercury, el tercer campo de `CONNECTED` (`500` o `BW500`) y los nombres de
  campos del JSON de Pat (`MID`, `Subject`, `Files`, `Name`, `Body`).
- Por eso las respuestas se guardan crudas y los criterios que dependen de esos formatos
  quedan en `null` o `no_determinado` en lugar de adivinar.

## Revisión externa y cambios
Una revisión independiente del commit inicial (inspección estática, sin ejecutar Mercury ni Pat)
llevó a estos cambios, cada uno comprobado antes contra el código y con simuladores:
- Cada comando de Mercury se juzga por su respuesta `OK` / `WRONG` (confirmadas en `docs/TNC.md`
  de `mercuryv2`); `BW` tras `CONNECT` puede ser rechazado sin ser un error (resultado de E0-2).
- `BUFFER 0` solo vale si su marca de tiempo es posterior al inicio de la escritura.
- `CONNECTED <origen> <destino> <ancho>` se valida completo (ancho e indicativos).
- `netstat` se interpreta sin depender del idioma de Windows (en español dice `ESCUCHANDO`).
- Pat: un solo mensaje por UUID, un solo adjunto con tamaño y SHA-256 correctos, y campo `date`
  al crear mensajes (**según la revisión externa; no verificado por nosotros**).
- Evidencia ampliada: índice de tiempos de lo enviado, estado de cada socket, entorno al final y
  copia con hash de `mercury.ini`, configuración y logs (sección `[registrar]` del TOML).
- Rechazado: usar la bandeja de salida solo como observación en `queue` (v6 pide comprobarla
  como paso previo; no prueba entrega).
- Pendiente de observar en E0-1: si enviar `DISCONNECT` justo tras `BUFFER 0` corta algo en B.

Códigos de salida: `send` = 0 significa solo "sesión emisora completada", nunca entrega.

## Primeras ejecuciones sugeridas (E0-1)
    PC B:  python mercury_smoke_client.py listen --tag E0-1_16B --payload-size 16
    PC A:  python mercury_smoke_client.py send   --tag E0-1_16B --payload-size 16
    python smoke_cross_check.py <result.json de A> <result.json de B>
Repetir con 1024 y 10240 bytes.
