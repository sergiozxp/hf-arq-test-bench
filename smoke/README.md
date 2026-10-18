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

## Primeras ejecuciones sugeridas (E0-1)
    PC B:  python mercury_smoke_client.py listen --tag E0-1_16B --payload-size 16
    PC A:  python mercury_smoke_client.py send   --tag E0-1_16B --payload-size 16
    python smoke_cross_check.py <result.json de A> <result.json de B>
Repetir con 1024 y 10240 bytes.
