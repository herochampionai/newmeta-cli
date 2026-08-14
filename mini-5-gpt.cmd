@echo off
if "%~1"=="" (
    python "%~dp0cli.py" --chat -p mini-5-gpt
) else (
    python "%~dp0cli.py" %* -p mini-5-gpt
)
