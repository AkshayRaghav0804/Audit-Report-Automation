@echo off
setlocal
pushd "%~dp0" >nul
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\setup_local.ps1"
set "SETUP_EXIT_CODE=%ERRORLEVEL%"
popd >nul
if not "%SETUP_EXIT_CODE%"=="0" echo Setup failed. Review the message above before trying again.
exit /b %SETUP_EXIT_CODE%
