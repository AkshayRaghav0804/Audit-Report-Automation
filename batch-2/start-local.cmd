@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "VENV_PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo ERROR: The project virtual environment was not found at:
    echo   %VENV_PYTHON%
    echo Create it and install the project before running this launcher.
    exit /b 1
)

pushd "%PROJECT_ROOT%" >nul
"%VENV_PYTHON%" -m scripts.local_launcher
set "LAUNCH_EXIT_CODE=%ERRORLEVEL%"
popd >nul

exit /b %LAUNCH_EXIT_CODE%
