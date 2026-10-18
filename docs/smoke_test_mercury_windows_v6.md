# Smoke test v6: Mercury 1.9.19 y Pat 1.0.0 en Windows

> **Alcance: todo en Windows, sin simulador de canal.** Documento independiente de `banco_pruebas_vara_mercury_v4.md`.
> Sus resultados son requisito de la Fase 0 del banco.
>
> **Estado:** E0 (exploración) pendiente de ejecutar. H1 y H2 no se ejecutan hasta cerrar E0 según la regla de cierre.
> Los resultados de E0 son exploratorios. No cuentan como smoke test aprobado ni como medición de rendimiento.

## Objetivo
Comprobar que Mercury 1.9.19 (v2) y Pat 1.0.0 funcionan en Windows entre dos PCs, con enlace de audio directo,
antes de cualquier medición. No se mide rendimiento. Se verifica que el sistema funciona y que la configuración
queda registrada.

## Versiones fijas
- **Mercury:** 1.9.19, build oficial para Windows x64, ZIP oficial (no instalador). Se registra el SHA-256 del
  ZIP descargado y el SHA-256 de `mercury.exe`. Mismo archivo en ambos PCs.
- **Pat:** 1.0.0, binario oficial para Windows. Se registra el SHA-256 del archivo descargado. Mismo archivo en ambos PCs.
- Fuente de las versiones: página de releases de `Rhizomatica/mercury` y de `la5nta/pat`, descargadas en el navegador.

## Requisitos
- Dos PCs con Windows x64, sin radios.
- Dos interfaces de audio USB, una por PC.
- Cableado en dos trayectorias. Nunca se unen salidas entre sí ni entradas entre sí:
  - Trayectoria 1: salida de la interfaz de A → entrada de la interfaz de B.
  - Trayectoria 2: salida de la interfaz de B → entrada de la interfaz de A.
  - Niveles: el pico de salida de cada PC no supera el 80 % del fondo de escala. Los niveles se registran.
  - Sin monitoreo de la entrada en la misma interfaz, para evitar retroalimentación.
- Topología: cada PC corre su propio Mercury y, en H2, su propio Pat. Pat se conecta al Mercury de su mismo PC
  por `localhost`. Los dos Mercury se comunican solo por el audio.
- Ninguna otra aplicación de audio abierta. Mejoras de audio de Windows desactivadas. Suspensión desactivada.

## Reglas de registro para toda la prueba
- Capturas crudas de los sockets 8300 y 8301 en cada PC.
- Respuestas completas de la API de Pat (código HTTP y cuerpo).
- Archivos enviados y recibidos, con su SHA-256.
- Logs de Mercury y de Pat.
- Configuración usada (`mercury.ini` y de Pat), con su SHA-256.
- Carpeta de resultados: `results\smoke\` para la prueba formal y `results\e0\` para la exploración.

## E0: exploración previa (no cuenta como smoke test)

### E0-1: transporte de payload por el socket 8301
Verifica la cabecera y el envío de datos antes de usarlos en H1.

- **Payloads:** 16 B, 1 KB y 10 KB. Los dos últimos superan varios bloques de lectura TCP.
- **Cabecera de aplicación** (nuestra, no de Mercury): 16 bytes con la longitud del payload (8 bytes) y el SHA-256
  truncado (8 bytes), seguida del payload.
- **Fin de lectura del receptor:**
  - Termina cuando ha recibido exactamente la longitud declarada en la cabecera.
  - Un fragmento corto no se trata como payload incompleto.
  - Si pasan 60 segundos sin bytes nuevos después de `CONNECTED` y el payload no está completo, es un fallo por timeout.
  - Después de completar el payload no debe llegar ningún byte extra. Si llega, es un fallo.
- **Registro:** bytes recibidos por bloque y SHA-256 del payload completo.
- **Aprobado:** el hash coincide en los tres tamaños. No hay bytes en 8301 fuera de la sesión `CONNECTED`. Las
  capturas de 8300 y 8301 quedan guardadas.
- **Fallo:** hash distinto, bytes extra, desconexión antes de completar, o timeout.

### E0-2: orden de BW y negociación entre pares
Verifica el orden de `BW500`, de `LISTEN ON` y la línea `CONNECTED`.

- **Requisito:** dos Mercury con audio entre ellos. Una prueba con una sola instancia no confirma negociación entre pares.
- **Sesiones independientes:** cada variante se ejecuta en una sesión nueva. Entre variantes se envía `DISCONNECT`
  y se confirma que no hay estado `CONNECTED` antes de seguir. No se encadenan variantes en la misma sesión.
- **Variantes:**
  - V1: `BW500` antes de `CONNECT`.
  - V2: `BW500` después de `CONNECT`, si la sesión lo acepta.
  - V3: `LISTEN ON` en el receptor antes del `CONNECT` del emisor.
- **Registro en cada variante:** respuesta a cada comando, línea `CONNECTED` completa (incluido el tercer campo,
  con el formato tal como aparece), ancho configurado en Pat y Mercury en ese momento, y si el comando de BW fue aceptado.
- **Aprobado:** `CONNECTED` con el ancho esperado y respuestas sin error. La variante queda clasificada como
  aceptada o rechazada.
- **Fallo:** no hay `CONNECTED`, o el ancho reportado no coincide con el configurado.

### E0-3: mensaje y adjunto por la API de Pat
Verifica que el adjunto recibido corresponde al envío.

- **Procedimiento:**
  1. En el PC A, crear el mensaje con `POST /api/mailbox/out` (multipart): campo `to` con el destinatario, `subject`
     con un UUID de corrida, y adjuntos en el campo `files`. Adjuntos: 200 B y 10 KB, en dos mensajes separados.
  2. Verificar con `GET /api/mailbox/out` que el mensaje está en la bandeja de salida.
  3. Llamar `POST /api/connect?url=varahf:///<CALL-B>`. Registrar código HTTP y cuerpo.
  4. En el PC B, leer `GET /api/mailbox/in`. Identificar el mensaje por el UUID del asunto.
  5. Descargar el adjunto con `GET /api/mailbox/in/{mid}/{attachment}` y calcular su SHA-256.
- **Aprobado:** el adjunto aparece en la bandeja de entrada de B, con el UUID de corrida y el mismo SHA-256 que
  el archivo enviado.
- **Fallo:** el mensaje no aparece en B, el hash no coincide, o la sesión responde HTTP 500.
- El estado de `out` tras la sesión es una observación. No es criterio de entrega.

### E0-4: ACK de aplicación en sesión de retorno
Define y prueba el ACK de aplicación. Requiere E0-3 aprobado.

- **Quién lo genera:** B, después de verificar el hash del adjunto (E0-3, paso 5).
- **Cómo se pone en cola:** B crea un mensaje con `POST /api/mailbox/out`, con `to` igual a CALL-A, `subject`
  `ACK run=<UUID>` y `body` con el UUID completo y el SHA-256 completo del adjunto.
- **Cómo se envía:** B llama `POST /api/connect?url=varahf:///<CALL-A>`. El Pat de A debe tener `listen` activo
  para `varahf` y Mercury de A debe estar escuchando (`LISTEN ON`).
- **Cómo se verifica:** A lee `GET /api/mailbox/in` y busca el mensaje cuyo asunto contiene el UUID de la corrida.
  Compara el SHA-256 del body con el del envío original.
- **Tipo de sesión:** siempre sesión de retorno, porque el hash se verifica al terminar la sesión de datos.
  Una entrega del ACK en la misma sesión no se prueba aquí. Si se quisiera probar, se documentaría como caso nuevo.
- **Aprobado:** el ACK llega a A con el UUID y el hash correctos, y la sesión de retorno queda registrada.
- **Fallo:** el ACK no llega, el UUID o el hash no coinciden, o la sesión falla.
- E0-4 no es criterio del smoke test formal. Su resultado se usa para definir el ACK en el procedimiento de medición.

## Regla de cierre de E0
E0 se cierra solo cuando se cumple una de estas dos opciones para cada ensayo:
1. **Aprobado.** Queda con sus capturas y resultados.
2. **Fallo con causa resuelta.** Se registra la causa, se corrige y se repite el ensayo hasta aprobarlo.
3. **Fallo con decisión de diseño.** Se registra la causa, la decisión expresa de cambiar el diseño, su motivo, y
   qué parte del procedimiento cambia. Esa decisión queda aprobada antes de continuar.

Un fallo sin causa documentada, o sin decisión de diseño, mantiene E0 abierto. Cuando E0 cierra, se redacta el
procedimiento formal (v7) de H1 y H2, con los pasos que E0 validó.

## H1: Mercury 1.9.19 entre dos PCs (formal, después de E0)
Usa el cliente validado en E0-1 y la secuencia validada en E0-2.

1. Verificar con `netstat -ano` que Mercury escucha en `localhost:8300` y `localhost:8301` en cada PC.
2. Receptor (PC B): `MYCALL <CALL-B>`, `BW500`, `LISTEN ON`.
3. Emisor (PC A): `MYCALL <CALL-A>`, `BW500`, `CONNECT <CALL-A> <CALL-B>`. Espera `CONNECTED`.
4. Emisor: escribe en 8301 la cabecera y el payload de 200 B. Espera `BUFFER 0` y envía `DISCONNECT`.
5. Receptor: lee 8301 hasta completar la longitud declarada y verifica el SHA-256.
6. Repetir tres veces. Cada repetición parte desconectada.

Aviso: `BUFFER 0` indica confirmación a nivel ARQ, no entrega de aplicación. La entrega se demuestra con el hash en B.

## H2: Pat 1.0.0 con `varahf` sobre Mercury 1.9.19 (formal, después de E0)
Configuración de Pat, verificada en el código fuente de Pat 1.0.0:
- `varahf.addr = "localhost:8300"`. El puerto de datos es 8300 + 1.
- `varahf.bandwidth = 500`. El ancho de banda va aquí, no en la URL. Cambiarlo exige reiniciar Pat (pendiente de
  confirmar si basta con reiniciar el módem).
- `varahf.ptt_ctrl = false`, sin `rig`.
- `listen = ["varahf"]`.

Procedimiento:
1. Iniciar Mercury en ambos PCs antes que Pat (H1, paso 1).
2. Iniciar `pat.exe` en ambos PCs con `varahf` activo. Verificar con `netstat -ano`.
3. Crear el mensaje como en E0-3, paso 1, con un UUID de corrida nuevo.
4. Llamar `POST /api/connect?url=varahf:///<CALL-B>`. Registrar código HTTP y cuerpo.
5. Verificar en la bandeja de entrada de B el adjunto, su UUID y su SHA-256 (E0-3, pasos 4 y 5).
6. Repetir tres veces. Cada repetición parte desconectada (`POST /api/disconnect` antes de la siguiente).

El ACK de aplicación no forma parte de H2. Su procedimiento y resultado se obtienen en E0-4.

`NumReceived` de la respuesta a `/api/connect` cuenta mensajes nuevos en la bandeja de entrada de A. No indica
que el mensaje enviado haya llegado a B.

## Criterios de salida (solo para el smoke test formal)
Se aprueba el smoke test solo si se cumplen todos:
- Versiones de Mercury y Pat coinciden con las fijadas, y sus SHA-256 quedan registrados en ambos PCs.
- E0 cerrado según la regla de cierre, con sus capturas, y el procedimiento v7 derivado de E0.
- H1: tres transferencias con SHA-256 verificado entre los dos PCs, sin reinicio manual de Mercury.
- H1: el ancho de banda de `CONNECTED` coincide con el configurado (BW500 o BW2300), y queda registrado tal como aparece.
- H2: tres sesiones de Pat con UUID y SHA-256 verificados en el receptor.
- Todos los logs, capturas, respuestas HTTP y configuraciones con hash guardados en `results\smoke\`.

## Si algo falla
- No se modifica Mercury ni Pat. Se registra el error completo, el log y la configuración usada.
- Un fallo de conexión se reporta como fallo del sistema, no como resultado de la prueba.
- Si H2 falla y H1 aprueba, la causa se busca primero en la configuración de Pat (`varahf`). Si persiste, se documenta
  la diferencia y se evalúa Pat-Vara o un puente propio como respaldo, con su costo declarado.
- Si E0-1 o E0-2 fallan sin causa resuelta ni decisión de diseño, no se avanza a H1.

## Pendientes que no bloquean E0
- Confirmar en la página de releases la última versión de Mercury 1.9.19 y el nombre exacto de los archivos del ZIP.
- Confirmar la opción de listado de dispositivos de audio de `mercury.exe`.
- Confirmar `method=none` y las claves de `mercury.ini` en la documentación de la v2.
- Confirmar si basta con reiniciar el módem para cambiar `varahf.bandwidth`.
- Confirmar el formato exacto de los campos del tercer término de `CONNECTED` (`500` o `BW500`).

## Verificado en revisión previa
- Puertos 8300 y 8301, comandos y semántica de datos y eventos, en `docs/TNC.md` de `mercuryv2`.
- Transporte `varahf` de Pat 1.0.0, claves `addr`, `bandwidth`, `ptt_ctrl` y `rig`, y semántica de `POST /api/connect`,
  en el código fuente de Pat 1.0.0.
- Destinatario con `to`, adjuntos con `files` y descarga de adjuntos por ruta, en el código fuente de Pat 1.0.0.

## Registro de resultados
| Prueba | Tipo | PC | Versión | SHA-256 binario | Resultado | Notas |
|---|---|---|---|---|---|---|
| E0-1 (16 B, 1 KB, 10 KB) | Exploración | A/B | | | | |
| E0-2 (V1, V2, V3) | Exploración | A/B | | | | |
| E0-3 (mensaje y adjunto) | Exploración | A/B | | | | |
| E0-4 (ACK en sesión de retorno) | Exploración | A/B | | | | |
| H1-1 a H1-3 | Formal | A/B | | | | |
| H2-1 a H2-3 | Formal | A/B | | | | |
