@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Run HF-ARQ-Modem-Smoke.exe instead.
  pause
  exit /b 1
)
py -3 smoke_gui.py
if errorlevel 1 pause
