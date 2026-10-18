# Guía del operador (Windows, solo mouse)

Para los dos PC de las pruebas. No hay que escribir comandos ni instalar Python.

## 1. Descargar (una vez por PC)

1. Entre a `https://github.com/sergiozxp/hf-arq-test-bench/releases` con la cuenta del proyecto
   (el repositorio es privado).
2. En la versión más reciente, en **Assets**, descargue `HF-ARQ-Smoke.exe`
   (y `HF-ARQ-Smoke.exe.sha256`, que sirve para comprobar que la descarga no se corrompió).
3. Guárdelo en una carpeta propia, por ejemplo `C:\HF-ARQ\`. Junto al programa se creará la carpeta
   `HF-ARQ-Smoke-datos` con la configuración y los resultados.

**Si Windows muestra «Windows protegió su PC»:** el programa no está firmado. Pulse
**Más información** y luego **Ejecutar de todas formas**. Si el antivirus lo bloquea, consulte
con quien administra el equipo antes de permitirlo; el código fuente completo está en el repositorio.

## 2. Usarlo

1. Doble clic en `HF-ARQ-Smoke.exe`.
2. En «1. Este PC»: elija **PC A (emisor)** o **PC B (receptor)**, escriba su indicativo y el del otro PC.
3. En «2. Prueba»: elija el programa (Mercury o Pat) y la prueba; pulse **EJECUTAR**.
4. Lea el resultado en color: verde (aprobado), naranja (falta revisar o cruzar con el otro PC), rojo (fallo).
5. Al terminar, **Comprimir resultados (.zip)** y lleve el archivo del PC B al A (o al revés) para
   **Cruzar resultados A y B**.

Mercury y Pat se inician a mano antes de pulsar EJECUTAR; este programa no los arranca.

## 3. Qué NO está probado

Cada versión publicada pasó, antes de publicarse, las pruebas automáticas con Mercury y Pat
**simulados** (las ejecuta GitHub en Windows; una versión que no las pase no se publica).
Todavía no se ha ejecutado con Mercury ni Pat reales: las pruebas E0-1 y E0-2 existen para eso.
