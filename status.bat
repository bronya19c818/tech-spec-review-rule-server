@echo off
REM Show service status: running, embedding model, index size.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [status] environment missing, run setup.bat first
  exit /b 1
)
".venv\Scripts\python.exe" server.py status
endlocal
