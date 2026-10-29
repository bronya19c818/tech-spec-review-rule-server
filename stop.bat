@echo off
REM Stop the rule service, including the PostgreSQL child process.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [stop] environment missing
  exit /b 1
)
".venv\Scripts\python.exe" server.py stop
endlocal
