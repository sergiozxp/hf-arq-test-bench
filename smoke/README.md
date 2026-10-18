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

Cada ejecución crea `results/<e0|smoke>/<run_id>/` con `events.jsonl`, bytes crudos de los
sockets (con índice de tiempos de lo recibido y de lo enviado), `environment.json` y
`environment_fin.json`, `netstat` inicial y final, la configuración usada y `result.json`.
El QUÉ HACE / QUÉ NO HACE / POR QUÉ está al inicio de cada script.

## Qué es cada estado
- `arq_confirmado` (solo emisor): Mercury informó `BUFFER 0` tras la escritura. NO es entrega.
- `entregado_integro` (solo receptor): el payload se leyó completo y su SHA-256 coincide.
- `ok_local` (Pat, `queue`/`ack-queue`): el mensaje está en la bandeja de salida LOCAL.
  No es entrega; esa solo la decide `check-in` por hash.
- Cruce: `aprobado` / `fallo` / `incompleto` (entrega verificada pero falta evidencia que v6
  exige: un archivo de `[registrar]` configurado que no se pudo copiar).
- Códigos de salida: `send` = 0 significa solo "sesión emisora completada", nunca entrega.

## Confirmado en documentación o código fuente (no probado con binarios reales)
- Mercury (`docs/TNC.md`, rama `mercuryv2`): comandos terminados en `\r`; respuestas `OK` o
  `WRONG`; evento `CONNECTED <origen> <destino> <ancho>` (ancho 500, 2300 o 2750; origen =
  quien inició la sesión); `BUFFER 0` = todos los datos confirmados por el par.
- Pat 1.0.0 (`api/mailbox.go`, `postOutboundMessageHandler`): exige exactamente un campo
  `date`, lo lee con `time.RFC3339` y su falta da HTTP 400; el asunto se toma de `subject` y
  los adjuntos de `files`. Los nombres `MID`, `Subject`, `Files`, `Name`, `Body` fueron
  confirmados por una revisión externa.

## NO probado con Mercury ni Pat reales
- Que el binario de Windows produzca exactamente las líneas documentadas (incluido el SSID
  en `CONNECTED` y el orden de los indicativos en el receptor).
- Que Mercury responda OK/WRONG una vez por comando y en orden (se verifica en E0-2).
- Que Mercury acepte o rechace `BW` tras `CONNECTED` (variante V2).
- La secuencia real de `BUFFER` (si informa valores positivos antes de 0); se anota como
  observación en `buffer_positivo_antes_de_cero`.
- Que `DISCONNECT` justo tras `BUFFER 0` no corte nada en B (a observar en E0-1).
- Que Pat conserve asunto y adjuntos durante la transferencia real.
- Que los logs de Mercury y Pat se puedan copiar con los programas abiertos (si no, la
  corrida queda `evidencia_incompleta`; los logs vivos son instantáneas, no archivos cerrados).
- El formato de `netstat` en cada Windows del banco (en Windows en español el estado puede
  aparecer traducido; por eso se detecta por la dirección remota vacía, no por la palabra).
- La salida de `powercfg` y `Get-PnpDevice`; si fallan quedan como advertencia visible.

## Revisiones externas y cambios
Primera revisión (commit 8f3ac9e) y segunda (commit 8bc4ba6): cada hallazgo se comprobó antes
contra el código y con simuladores de la lógica (no son Mercury ni Pat). Aceptados: juicio de
`OK`/`WRONG` por comando; `BUFFER 0` posterior a la escritura; `CONNECTED` estricto (4 campos);
`DISCONNECT` juzgado; `BW` del receptor tras `CONNECTED`; un solo mensaje y un solo adjunto con
tamaño y SHA-256; HTTP 200 antes de interpretar la bandeja; evidencia final también en Pat;
nombres de copias con la clave de `[registrar]`; `ok_local`. Rechazado: quitar la comprobación
de la bandeja de salida en `queue` (v6, E0-3 paso 2 la exige como paso previo) y añadir una
barrera de entrega antes de `DISCONNECT` (v6, H1 paso 4).

## Primeras ejecuciones sugeridas (E0-1)
    PC B:  python mercury_smoke_client.py listen --tag E0-1_16B --payload-size 16
    PC A:  python mercury_smoke_client.py send   --tag E0-1_16B --payload-size 16
    python smoke_cross_check.py <result.json de A> <result.json de B>
Repetir con 1024 y 10240 bytes.
