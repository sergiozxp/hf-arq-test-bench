# hf-arq-test-bench

Banco de pruebas reproducible para comparar la entrega de mensajes por HF ARQ en tres
configuraciones, con canal simulado y sin radios, todo en Windows:

1. VarAC + VARA (sin licencia)
2. VarAC + VARA (con licencia)
3. Mercury 1.9.19 + Pat 1.0.0

**Estado: borrador de trabajo, repositorio privado.** Nada de lo que hay aquí está probado
todavía con Mercury, Pat, VARA ni VarAC reales; los scripts se validaron solo contra
simuladores de la lógica. No se presentan resultados.

## Contenido
- `docs/banco_pruebas_vara_mercury_v4.md`: diseño del banco (fuente de verdad del método).
- `docs/smoke_test_mercury_windows_v6.md`: smoke test previo (E0, H1, H2) y criterios de salida.
- `smoke/`: scripts del smoke test (ver `smoke/README.md`).

## Qué es norma y qué es diseño propio
- Canal ionosférico: modelo de tipo Watterson, comparado con ITU-R F.1487 (la tabla de
  correspondencia parámetro a parámetro está pendiente; hasta entonces no se llama
  "conforme a F.1487").
- Integridad: SHA-256 (FIPS 180-4).
- Métricas de entrega, ACK de aplicación, árbitro semidúplex y estadística: diseño de los
  autores, descrito en `docs/`.

## Licencia
Código: MIT (ver `LICENSE`). Documentos y datos: licencia por decidir antes de hacer
público el repositorio.

## Descarga para Windows (sin instalar Python)

`HF-ARQ-Smoke.exe` se compila en GitHub Actions y se publica en *Releases* con su SHA-256.
Guía de uso para el operador: [docs/guia_operador_windows.md](docs/guia_operador_windows.md).
Las pruebas del lanzador usan Mercury y Pat **simulados** (`tests/`); no sustituyen a las
pruebas E0 con los programas reales.
