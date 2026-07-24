@echo off
REM NewMeta Setup - Run once to add to PATH
set "NEWMETA_DIR=%~dp0"
set "PATH=%NEWMETA_DIR%;%PATH%"
echo NewMeta added to PATH for this session
echo.
echo To use: newmeta --chat
echo.
echo To add permanently, run PowerShell as Admin:
echo   [System.Environment]::SetEnvironmentVariable("PATH", "$env:PATH;C:\path\to\NewMeta", "User")
pause