@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run setup.cmd first to create the application environment.
    exit /b 1
)
call "%~dp0start-local.cmd"
exit /b %ERRORLEVEL%
