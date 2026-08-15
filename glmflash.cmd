@echo off
if "%~1"=="" (
    python "C:\Users\youha\Desktop\Codes\pika poke\NewMeta\cli.py" --chat -p glmflash
) else (
    python "C:\Users\youha\Desktop\Codes\pika poke\NewMeta\cli.py" %* -p glmflash
)
