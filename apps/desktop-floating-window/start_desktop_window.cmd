@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%SCRIPT_DIR%desktop_floating_window.py"
) else (
  start "" python "%SCRIPT_DIR%desktop_floating_window.py"
)
