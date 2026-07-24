@echo off
if "%~1"=="" (
    call "%~dp0newmeta.cmd" agents models
) else (
    call "%~dp0newmeta.cmd" agents %*
)
