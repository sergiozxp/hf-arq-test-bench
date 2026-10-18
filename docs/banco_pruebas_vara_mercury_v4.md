# Banco de pruebas VARA vs Mercury + Pat — versión 4

> \*\*Alcance: todo el banco se ejecuta en Windows.\*\* Extremos, simulador de canal y arnés de medición.
> Linux no forma parte del banco. Cualquier paso hecho en Linux (por ejemplo, la VM de verificación)
> queda fuera de la medición y se declara en el informe.

## Decisiones fijas (v4)

* **Mercury:** versión 2, **1.9.19**, build oficial para Windows x64 (instalador o ZIP). SHA-256 y
nombre del archivo registrados en `environment.json`. Versión pendiente de confirmar en la página
de releases antes de la primera corrida.
* **Pat:** **1.0.0**, binario oficial para Windows, transporte `varahf` hacia Mercury
(cada PC usa su Mercury local: `localhost:8300` comandos, `8301` datos). Sin Pat-Vara y sin puente propio. El puente queda solo como
respaldo si H2 falla, y se reportaría como cambio de alcance.
* **VARA HF:** versión fija, sin licencia y con licencia. Número exacto pendiente de registrar.
* **VarAC:** versión fija pendiente de registrar.
* **Simulador:** en Windows. `ch.exe` compilado con MSYS2 (pendiente de verificar). Audio por WASAPI con
interfaces USB reales.

## Contexto

Soy radioaficionado (XQ2CG). Quiero medir con datos propios, sin radios y con un simulador de canal, el
tiempo real de entrega de mensajes de tres combinaciones ("brazos"):

1. `Mercury + Pat` (Mercury 1.9.19 de Rhizomatica, GPL-3.0; Pat 1.0.0 de la5nta, MIT, en modo P2P)
2. `VarAC + VARA sin licencia`
3. `VarAC + VARA con licencia`

No existen mediciones independientes recientes que comparen estas combinaciones. El objetivo es generarlas
de forma reproducible y metodológicamente defendible.

## Matriz a llenar (por cada brazo)

* Filas = condiciones de canal (perfiles candidatos, provisionales hasta la Fase 0):

  * Normal: perfil candidato `MPG`, SNR nominal +10 dB
  * Malas: perfil candidato `MPP`, SNR nominal +3 dB
  * Muy malas: perfil candidato `MPD`, SNR nominal −3 dB
(valores configurables)
* En cada corrida se registran los parámetros efectivos del perfil (retardo, Doppler, ruido) y la versión
de codec2. Solo tras la Fase 0 se los puede llamar formalmente "CCIR good", "poor" o "disturbed", y solo
si la implementación concreta de `ch` corresponde a esos parámetros.
* Columnas = carga:

  * `texto\_200B` y `texto\_10KB`: texto realista, mismo archivo para todos los brazos. Miden el resultado de
usuario (se permite la compresión de cada aplicación).
  * `binario\_10KB`: determinista (semilla fija) e incompresible. Mide el enlace. Fase 2.
  * Declarar en cada corrida qué compresión estaba activa (B2F, gzip de Pat, VarAC).
  * Los archivos de texto se codifican en UTF-8 sin BOM y con finales de línea LF. Cuando una aplicación
normalice el texto (CRLF, Unicode, espacios), la validación se hace sobre un archivo adjunto o un
manifiesto de bytes, no sobre el texto mostrado en pantalla.
* Ancho de banda: BW500 y BW2300 son escenarios separados. Las combinaciones no permitidas (por licencia o
por módem) se reportan como N/A con su motivo, no se omiten.

  * Mercury en BW500 usa como máximo DATAC3 (según notas de 1.9.18). El brazo se reporta con ese techo.
  * VARA gratis en BW2300: N/A hasta confirmar el límite con EA5HVK.

### Tabla objetivo (Fase 1: BW500, texto)

Cada celda se llena solo con datos medidos. La columna de hipótesis es una comparación, no un dato.

|Propagación|Brazo|200 B: éxito / mediana entrega|10 KB: éxito / mediana entrega|Hipótesis previa (200 B / 10 KB), no verificada|
|-|-|-|-|-|
|Normal (MPG)|Mercury + Pat|||\~1–1,5 min / \~2–3 min|
|Normal (MPG)|VarAC + VARA gratis|||\~20–40 s / \~1,5–2 min|
|Normal (MPG)|VarAC + VARA pagado|||\~20–30 s / \~1 min|
|Malas (MPP)|Mercury + Pat|||\~2 min / \~5–6 min|
|Malas (MPP)|VarAC + VARA gratis|||\~30–60 s / \~3–5 min|
|Malas (MPP)|VarAC + VARA pagado|||\~30–60 s / \~2–4 min|
|Muy malas (MPD)|Mercury + Pat|||\~3–5 min o no conecta / \~15–20 min o no completa|
|Muy malas (MPD)|VarAC + VARA gratis|||\~1–2 min / \~15–40 min|
|Muy malas (MPD)|VarAC + VARA pagado|||\~1–2 min / \~15–40 min|

Dentro de cada celda se reporta: `n\_total`, `n\_éxito`, `n\_falla` y tasa de éxito con intervalo de Wilson;
mediana y p90 de `t\_entrega\_recibida` solo con éxitos (p90 solo con al menos 5 éxitos); mediana de
`t\_conexion` y de `t\_transferencia` por separado; distribución de `nivel\_arranque` y de
`ancho\_banda\_confirmado`; y mediana de Kaplan-Meier solo si la tasa de éxito supera el 50 %.

## Arquitectura (todo Windows)

```
PC A (Windows)                  PC simulador (Windows)                 PC B (Windows)
Pat 1.0.0 + Mercury 1.9.19  ⇄   interfaz USB  ⇄  ch.exe (A→B)   ⇄   interfaz USB  ⇄  Pat 1.0.0 + Mercury 1.9.19
  o VarAC + VARA                 interfaz USB  ⇄  ch.exe (B→A)   ⇄   interfaz USB     o VarAC + VARA
```

* Cada extremo tiene una interfaz de audio USB de entrada y otra de salida, conectadas al simulador.
* El simulador tiene dos procesos independientes, A→B y B→A, con fading distinto por dirección.
* Cada PC de medición corre su propio Mercury, y Pat (en `Mercury + Pat`) se conecta a él por `localhost`.
Los dos Mercury se comunican solo por el audio simulado.

### Canal

* Cada proceso lee de una interfaz de entrada por WASAPI y escribe a una de salida. `ch.exe` trabaja por
stdin/stdout en tiempo real; el orquestador en Python conecta los procesos con tuberías.
* Remuestreo 48 kHz ↔ 8 kHz en Python (`scipy.signal.resample\_poly`) o con `sox.exe`. Lo que se use queda
registrado con su versión.
* WASAPI en modo exclusivo, con frecuencia de muestreo y búfer fijados. Valores registrados en
`environment.json`.
* El simulador registra con marca de tiempo la actividad TX de cada estación.
* La detección de actividad TX se hace sobre el audio de entrada al simulador, antes de `ch`, del AWGN y del
fading. Si hay señal PTT disponible, se usa preferentemente; si no, un detector de energía con umbral,
histéresis y tiempo de retención configurables. Nunca se infiere actividad desde la salida de `ch`.

### Semidúplex

* Un árbitro de canal en Python decide la ocupación. Mientras una estación transmite, incluido el tiempo de
guarda TX/RX configurable (por defecto 50 ms), su receptor queda bloqueado.
* Cualquier solapamiento entre transmisiones de A y B se registra como colisión, y ninguna estación recibe
audio útil durante el solapamiento. Tras una colisión también se aplica el tiempo de guarda.

### SNR

* SNR nominal de AWGN = potencia RMS de la señal de referencia, medida a la entrada del receptor con
ganancia unitaria y fading desactivado, dividida por la potencia RMS del AWGN en una ventana de 3 kHz. El
fading se aplica después y se reporta aparte.
* Verificar si el fading de `ch` conserva la potencia media; si no, corregirlo en la calibración.
* Calibrar por módem y por ancho de banda, con la misma ruta WASAPI, frecuencia de muestreo y niveles que se
usarán en las corridas reales. Guardar la tabla de calibración. No presentar `--No` de `ch` como SNR sin esa
tabla.

### Reloj común

* El simulador ofrece un servicio de sincronización con su reloj monotónico. En Python, `time.perf\_counter\_ns`
usa `QueryPerformanceCounter` en Windows.
* Antes de cada corrida, A y B toman varias muestras, conservan la de menor RTT y registran el desfase y su
incertidumbre en `environment.json`.
* El registro de audio del simulador se usa directamente para `t\_ocupacion\_aire`; los eventos de aplicación
de A y B se convierten al reloj del simulador con ese desfase.
* NTP de Windows (w32time) en la red local se mantiene como respaldo, registrando también su incertidumbre.

### Red de control

* La red IP de control solo transporta sincronización, telemetría y recolección de resultados.
* Se bloquea toda conectividad IP directa entre las aplicaciones de A y B con reglas de Windows Firewall
(`netsh advfirewall`). Los payloads y ACK de prueba solo pueden recorrer el enlace de audio simulado.
* El dry run lo comprueba con `Get-NetTCPConnection` o `netstat -ano`, y guarda la salida.

## Dos niveles de medición

1. **Nivel 1, módem (automatizado):** cliente de referencia en Python (emisor y receptor) que habla con la
API TCP estilo VARA (comandos 8300, datos 8301). Debe funcionar contra VARA (sin y con licencia) y contra
Mercury 1.9.19 cambiando solo host y puertos, usando únicamente el subconjunto de comandos confirmado en
ambos.
2. **Nivel 2, aplicación completa:**

   * `Mercury + Pat`: automatizado con la API HTTP de Pat 1.0.0. El mensaje se crea en la bandeja de salida y
luego se llama a `POST /api/connect?url=varahf:///CALL`, que bloquea hasta el fin de la sesión. HTTP 500
indica fallo de sesión. HTTP 200 con `NumReceived` no prueba la entrega: el resultado se verifica en la
bandeja de entrada de B, por hash. El ancho de banda va en `varahf.bandwidth` de la configuración de Pat, no
en la URL; cada escenario BW500 o BW2300 es una configuración distinta (pendiente de confirmar si basta
reiniciar el módem). Configuración base: `ptt\_ctrl=false`, sin Hamlib, sin radios.
   * `VarAC + VARA`: automatización de la interfaz con pywinauto (nativo en Windows). Si no es viable, protocolo
manual paso a paso con planilla de registro. Tiempos desde los registros de VarAC si existen; el registro
de audio del simulador queda como evidencia auxiliar.
* Para "sin licencia", ambos extremos deben estar sin registrar.
* Interpretación: el nivel 1 compara módems y transporte. El nivel 2 compara sistemas completos (Pat P2P y
VarAC son aplicaciones distintas) y el informe debe llamarlo así.

## Entrega y ACK (común a todos los brazos y niveles)

* El receptor valida tamaño y SHA-256 de lo recibido. Solo entonces envía por el mismo enlace:
`ACK run=<12 hex> SHA256=<primeros 16 hex>`, con exactamente el mismo formato en todos los brazos. El UUID
completo y el hash completo quedan en el CSV.
* `BUFFER 0` NO es prueba de entrega. Según `docs/TNC.md` de Mercury, indica que el par confirmó los datos a
nivel ARQ. La prueba de entrega es la verificación de hash en B.
* En `VarAC + VARA`, la confirmación nativa de VarAC se registra adicionalmente, pero no reemplaza el ACK.
* En Pat P2P, verificar en el dry run si el receptor puede generar el ACK y ponerlo en cola antes de que Pat
cierre el intercambio. Si no, se envía en una sesión de retorno y se reporta explícitamente como tal.

## Métricas

* `t\_entrega\_recibida` es la métrica primaria de comparación y de throughput útil. `t\_entrega\_confirmada` es
secundaria.
* `t0\_total`: instante en que A solicita la conexión o el envío. Cada repetición parte desconectada: se exige
una conexión ARQ nueva por repetición.
* `t\_conexion`: desde `t0\_total` hasta que el enlace ARQ queda establecido.
* `t\_transferencia`: desde el enlace establecido hasta que B verifica el SHA-256.
* `t\_entrega\_recibida` = `t\_conexion` + `t\_transferencia`.
* `t\_entrega\_confirmada`: desde `t0\_total` hasta que A recibe el ACK.
* `t\_ocupacion\_aire`: por dirección, suma de las duraciones de actividad TX detectada. No incluye la guarda
semidúplex; se registra aparte el número de turnos TX por dirección.
* Si una API no permite separar `t\_conexion` de `t\_transferencia`, se reporta solo `t\_entrega\_recibida` y se
indica.
* Éxito o falla, con timeout de 30 minutos por intento (configurable); el timeout cuenta como falla.
* Throughput útil: bytes útiles / `t\_entrega\_recibida`.
* `nivel\_arranque` (solo VARA): nivel en que queda establecida la conexión (4, 3, 2 o 1) y número de NACK
recibidos. VARA intenta desde el nivel 4 y baja a 3, 2 y 1 si recibe NACK (changelog de VARA HF v4.5.5,
fuente de la comunidad, pendiente de verificar).
* `ancho\_banda\_confirmado`: valor que informa VARA en el comando `CONNECTED` (500, 2300 o 2750 Hz). Debe
coincidir con el brazo configurado. Si no coincide, la corrida es error de configuración: no cuenta ni como
éxito ni como falla, se reporta aparte y se explica.
* Las tasas en bps por nivel y ancho de banda no están fijadas. Se obtienen de EA5HVK o se miden; no se usan
cifras de tablas antiguas como dato del informe.
* Colisiones y reintentos observados.

## Estadística

* Tamaño muestral fijado antes de cada celda: 30 repeticiones; excepcionalmente 10 si el piloto estima una
duración superior a 10 minutos (esas celdas son exploratorias). No se detiene anticipadamente. Las corridas
piloto no entran en los resultados.
* Reportar por celda `n\_total`, `n\_éxito`, `n\_falla` y la tasa de éxito con intervalo de Wilson.
* Mediana, p90, mínimo y máximo se calculan solo entre intentos exitosos y se etiquetan como condicionales al
éxito. Si una celda tiene menos de cinco éxitos, no se informa p90.
* Advertencia en el informe: las métricas condicionales tienen sesgo de supervivencia. Siempre se presentan
junto a la tasa de éxito.
* Métrica secundaria: mediana de Kaplan-Meier, con timeouts como datos censurados. Solo se informa si la tasa de
éxito supera el 50 %; si no, se reporta "mediana > timeout".
* IC bootstrap para la mediana y el p90; intervalo de Wilson para la tasa de éxito.
* Aleatorizar el orden de los brazos dentro de cada condición, manteniendo la misma traza o semilla por
repetición.

## Reproducibilidad

* Para cada repetición `k` se usa una pareja nueva e independiente de trazas o semillas: una para A→B y otra
para B→A. Esa pareja se reutiliza, desde `t=0`, para todos los brazos de la repetición `k`. Guardar todas las
trazas usadas.
* Las trazas de fading se generan con un script reproducible en Windows: Octave para Windows con la semilla
explícita (`ch\_fading\_seeded.m`), o un port a Python si Octave no es estable. La herramienta y su versión
quedan registradas.
* Verificar si `ch.exe` permite fijar la semilla del ruido (parche `--seed`).
* Un archivo `environment.json` por corrida con: versión de Windows y build; versiones exactas de Mercury
1.9.19, Pat 1.0.0, VARA, VarAC y codec2; SHA-256 de cada binario; configuración de `mercury.ini` y de Pat
(con hash); dispositivos WASAPI por nombre y ID; modo exclusivo, frecuencia de muestreo y búfer; trazas o
semillas; y desfase e incertidumbre del reloj de A y B.

## Supuestos que debes VERIFICAR, no inventar

Antes de la Fase 0 se revisa la documentación o el código oficial y se registra qué se confirmó y qué no.

* **Mercury 1.9.19 para Windows:** que el ZIP oficial arranque en headless (`mercury.exe`), reconozca dispositivos
WASAPI o DirectSound y responda en 8300/8301. Pendiente.
* **Comandos TCP de Mercury** (verificado en `docs/TNC.md` de la rama `mercuryv2`): puertos 8300 y 8301; comandos
en texto terminado en `\\r` (`MYCALL`, `LISTEN ON`, `CONNECT <yo> <destino>`, `BW500`/`BW2300`, `DISCONNECT`);
los bytes del 8301 se encolan sin framing; eventos `CONNECTED`, `DISCONNECTED`, `BUFFER`, `REGISTERED`.
Pendiente: que el comportamiento de `BW500` y `LISTEN ON` coincida con lo descrito, en el dry run.
* **Pat 1.0.0 con `varahf`** (verificado en el código fuente de Pat 1.0.0): el transporte existe; `addr` apunta al
puerto de comandos y los datos van en +1; el ancho de banda sale de `varahf.bandwidth`; y `POST /api/connect`
bloquea y responde HTTP 500 ante fallo de sesión. Pendiente: que la sesión con Mercury 1.9.19 transfiera
datos y que el ACK de aplicación vuelva (H2). Lo afirma también el `SPEC.md` de hr-mail, pero no está probado.
* **`-S`:** no está disponible en Windows; el banco no lo usa.
* **Notas de 1.9.18 y 1.9.19:** confirmadas en el texto oficial. Sus cifras de rendimiento son de los autores y
se tratan como hipótesis a reproducir, no como dato.
* **`ch.exe` con MSYS2:** que compile, que corra en tiempo real sin pérdidas y que el fading conserve la potencia
media. Pendiente.
* **WASAPI:** latencia, estabilidad en modo exclusivo y ausencia de remuestreo inesperado. Pendiente.
* **VARA y VarAC:** versiones exactas, registro de sesión y viabilidad de automatización con pywinauto.
Pendiente.
* **Nivel de arranque y ancho de banda en `CONNECTED`:** fuente de la comunidad (changelog de VARA HF v4.5.5,
mensaje #7214). No verificado de forma independiente. Confirmar en el dry run.
* **Significado de "nivel 4"** en la respuesta de EA5HVK: pendiente de precisar (escala o bps). No se usa como
límite del banco hasta confirmarlo.
* **Compresión de Pat y de VarAC:** qué compresión está activa en cada aplicación. Pendiente.

Si algo no se puede verificar, se marca como pendiente en el README, en lugar de suponerlo.

## Entregables (Windows)

1. `README.md`: hardware, instalación en Windows, cableado USB, configuración WASAPI, calibración y ejecución.
2. `protocol\_compatibility.md`: matriz VARA vs Mercury 1.9.19, con comandos, eventos, terminadores, codificación,
orden de sockets, timeouts y máquina de estados, con evidencia de prueba. Reemplaza las tablas basadas en
versiones anteriores.
3. `simulator\\`: los dos procesos de canal en Python, `ch.exe` compilado con MSYS2, el árbitro semidúplex,
presets por condición, la calibración de SNR y el servicio de sincronización de reloj.
4. `modem\_bench\\`: cliente de referencia de nivel 1 en Python (emisor y receptor, con ACK y hash).
5. `pat\_bench\\`: runner de Pat P2P con la API HTTP de Pat 1.0.0.
6. `varac\\`: automatización con pywinauto o, si no es viable, `varac\_manual.md` con su planilla.
7. `payloads\\`: `texto\_200B`, `texto\_10KB` y el generador de `binario\_10KB`.
8. `results\\`: un CSV con una fila por intento, `environment.json` por corrida, y un script en Python que genere
la matriz resumen (con intervalos) en Markdown y gráficos.
9. `smoke\_test\_mercury\_windows.md`: documento aparte, con el criterio de salida de H1 y H2.

## Criterios de aceptación

* Dry run con canal limpio (sin ruido ni fading): el cliente de referencia completa la entrega con hash verificado
y ACK contra VARA y contra Mercury 1.9.19.
* Prueba prolongada de audio (30 a 60 minutos de audio limpio por toda la ruta) sin pérdidas de muestras,
desbordes de búfer, remuestreo inesperado ni AGC. Se desactivan las mejoras de audio de Windows, se fija
la frecuencia de muestreo en WASAPI modo exclusivo y la configuración queda registrada.
* Equipos en condiciones fijas: plan de energía sin suspensión, actualizaciones de Windows pausadas y sin otras
aplicaciones de audio abiertas. Se registra el estado.
* Las corridas son reanudables: si se corta, continúa sin repetir intentos ya hechos.
* Todo configurable por archivo (YAML o TOML), sin valores fijos en el código.
* Código simple y comentado en español; Python 3.11+ y scripts `.ps1` cuando haga falta; sin dependencias exóticas.

## Fases

* **Smoke test (documento aparte):**

  * H1: Mercury 1.9.19 entre dos PCs Windows, sin simulador.
  * H2: Pat 1.0.0 con `varahf` sobre Mercury 1.9.19, sin simulador.
* **Fase 0 (este documento):** verificación de supuestos, `protocol\_compatibility.md` para 1.9.19, build de
`ch.exe` con MSYS2, calibración WASAPI, sincronización de reloj, prueba prolongada de audio, dry run y piloto.
Requiere H1 y H2 aprobados.
* **Fase 1:** BW500 y cargas de texto, con los tres brazos.
* **Fase 2:** carga binaria y BW2300.

## Fuera de alcance

* Pruebas en el aire (se harán después para validar algunas celdas).
* Modificar VARA, VarAC o Mercury. Pat tampoco se modifica.
* Pat-Vara y puentes propios, salvo como respaldo declarado si H2 falla.
* Cualquier paso en Linux que afecte la medición.

## Historial de cambios respecto de v3

* Todo el banco pasa a Windows. El simulador, el audio, el reloj y la red de control se reescriben para Windows.
* Mercury 1.9.19 (v2) fijo, con build oficial para Windows; se eliminan los supuestos de build propio.
* Pat 1.0.0 con `varahf` sobre Mercury; se elimina Pat-Vara y el puente como camino principal.
* Matriz: Fase 1 limitada a BW500 y texto; BW2300 y binario quedan para la Fase 2.
* Se agrega la tabla objetivo con columna de hipótesis no verificada.
* Se agregan `nivel\_arranque` y `ancho\_banda\_confirmado` como métricas obligatorias, y la versión fija de VARA.
* El smoke test de Mercury se separa en su propio documento.
* Pat: se verifica en el código fuente que `varahf` es un transporte de Pat 1.0.0. El ancho de banda se configura
en `varahf.bandwidth`, no en la URL. El resultado de `/api/connect` no prueba la entrega.
* Mercury: puertos, comandos y semántica de `BUFFER` verificados en `docs/TNC.md` de `mercuryv2`.

