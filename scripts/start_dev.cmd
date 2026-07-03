@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

python "%PROJECT_ROOT%\scripts\start_dev.py"
