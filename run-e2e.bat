@echo off
REM End-to-end check: start server, import a fixture rule, query it back, write a report.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [e2e] environment missing, run setup.bat first
  exit /b 1
)
".venv\Scripts\python.exe" tests\e2e_server.py %*
endlocal
