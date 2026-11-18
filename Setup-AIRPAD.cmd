@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Install Python 3.12 x64 from python.org, then run this setup again.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m airpad install-model
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m airpad doctor
if errorlevel 1 goto failed
echo AIRPAD is ready. Open Start-AIRPAD.cmd.
pause
exit /b 0
:failed
echo Setup failed. Check the error above and your network connection.
pause
exit /b 1
