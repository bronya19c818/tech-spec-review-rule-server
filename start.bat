@echo off
REM Start the rule service (embedded PostgreSQL + registry API) in the background.
REM Logs land in data\logs\.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [start] environment missing, run setup.bat first
  exit /b 1
)
if not exist "data\logs" mkdir "data\logs"

".venv\Scripts\python.exe" server.py status >nul 2>nul
if not errorlevel 1 (
  echo [start] already running
  ".venv\Scripts\python.exe" server.py status
  exit /b 0
)

REM Local-only service: make sure no proxy intercepts loopback calls.
set "no_proxy="
set "http_proxy="
set "https_proxy="
set "all_proxy="

echo [start] launching
start "rule-server" /min cmd /c ".venv\Scripts\python.exe server.py serve >> data\logs\server.out.log 2>&1"

REM First run needs initdb; allow up to 90 seconds. ping is used instead of timeout
REM because timeout refuses redirected stdin.
for /l %%i in (1,1,45) do (
  ".venv\Scripts\python.exe" server.py status >nul 2>nul
  if not errorlevel 1 (
    echo [start] ready
    ".venv\Scripts\python.exe" server.py status
    exit /b 0
  )
  ping -n 3 127.0.0.1 >nul
)

echo [start] timed out; see data\logs\server.err.log and data\logs\postgres.log
exit /b 1
