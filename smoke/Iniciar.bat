@echo off
rem Alternativa SIN el .exe: doble clic para abrir la ventana con Python instalado.
rem Si usa HF-ARQ-Smoke.exe no necesita este archivo.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 smoke_gui.py
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw smoke_gui.py
  exit /b
)
echo No se encontro Python 3.11 o superior.
echo Instalelo desde https://www.python.org/downloads/ y marque "Add python.exe to PATH".
pause
