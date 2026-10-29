@echo off
REM First-run setup: create an isolated venv with the host Python 3.12 and install deps.
REM pgserver ships wheels up to cp312 only, so a newer Python will not work.
setlocal
cd /d "%~dp0"

set "HOSTPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%HOSTPY%" goto have_base
where py >nul 2>nul
if errorlevel 1 goto no_python
set "BASE=py -3.12"
goto make_venv

:have_base
set "BASE=%HOSTPY%"

:make_venv
if exist ".venv\Scripts\python.exe" goto install
echo [setup] creating venv with %BASE%
%BASE% -m venv .venv
if errorlevel 1 exit /b 1

:install
echo [setup] installing dependencies
set "PIP_PROXY="
if not "%RULE_SERVER_PIP_PROXY%"=="" set "PIP_PROXY=--proxy %RULE_SERVER_PIP_PROXY%"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q %PIP_PROXY% -r requirements.txt
if errorlevel 1 (
  echo [setup] pip failed. If this machine needs a proxy, set RULE_SERVER_PIP_PROXY first.
  exit /b 1
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [setup] wrote .env - fill TSR_EMBEDDING_API_KEY before starting.
)

echo [setup] done. Next: edit .env, then run start.bat
exit /b 0

:no_python
echo [setup] Python 3.12 not found. Install it, or run the DSH desktop app once to prepare its runtime.
exit /b 1
