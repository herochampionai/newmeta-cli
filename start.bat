@echo off
echo ================================================
echo   NewMeta v6.1 - Starting...
echo ================================================
echo.
echo Detecting best free model...
python -c "import urllib.request,json; r=urllib.request.urlopen('http://localhost:11434/api/tags',timeout=3); m=json.loads(r.read())['models']; print('Available:',', '.join([x['name'] for x in m])); print('Best:',m[0]['name'])"
if errorlevel 1 (
    echo.
    echo [ERROR] Ollama not running!
    echo Please install: https://ollama.com
    echo Then run: ollama serve
    pause
    exit /b 1
)
echo.
echo Starting chat...
python "%~dp0cli.py" --chat