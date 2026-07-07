@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "ENV_FILE=%PROJECT_ROOT%\backend\.env"
set "LWC_BACKEND_HOST=127.0.0.1"
set "LWC_BACKEND_PORT=18081"

if exist "%ENV_FILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if "%%A"=="LWC_BACKEND_HOST" set "LWC_BACKEND_HOST=%%~B"
    if "%%A"=="LWC_BACKEND_PORT" set "LWC_BACKEND_PORT=%%~B"
  )
)
set "LWC_BACKEND_HOST=%LWC_BACKEND_HOST:'=%"
set "LWC_BACKEND_PORT=%LWC_BACKEND_PORT:'=%"
if not defined LWC_BACKEND_BASE_URL set "LWC_BACKEND_BASE_URL=http://%LWC_BACKEND_HOST%:%LWC_BACKEND_PORT%"

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%SCRIPT_DIR%desktop_floating_window.py"
) else (
  start "" python "%SCRIPT_DIR%desktop_floating_window.py"
)