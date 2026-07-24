@echo off
REM Quick launch NewMeta from anywhere with Supervisor Protection
python -c "import subprocess; subprocess.run(['python', r'C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta\watcher.py'] + __import__('sys').argv[1:], shell=True)" %*