@echo off
setlocal
cd /d "%~dp0"
if exist "dist\AIRPAD-Windows\AIRPAD.exe" (
  start "AIRPAD" "dist\AIRPAD-Windows\AIRPAD.exe" --data-dir "%~dp0runtime-data" %*
  exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo AIRPAD environment not found. Run Setup-AIRPAD.cmd first.
  pause
  exit /b 1
)
start "AIRPAD" ".venv\Scripts\pythonw.exe" -m airpad %*
