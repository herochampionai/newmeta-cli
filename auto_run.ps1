#!/usr/bin/env pwsh
# NewMeta Auto-Runner - Run agents automatically without manual input

param(
    [string]$Task = "",
    [string]$Provider = "openrouter",
    [switch]$Chain,
    [switch]$Watch
)

$NEWMETA = "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta\watcher.py"

function Invoke-AutoTask {
    param($task, $provider)
    
    Write-Host "`n[Auto] Running: $task" -ForegroundColor Cyan
    
    $cmd = "python $NEWMETA -p $provider --chat -m `"$task`""
    cmd /c $cmd
}

# Single task mode
if ($Task) {
    Invoke-AutoTask -task $Task -provider $Provider
    exit 0
}

# Chain mode - run multiple agents together
if ($Chain) {
    Write-Host "[Chain] Running agent pipeline..." -ForegroundColor Yellow
    
    $tasks = @(
        "researcher: Find best practices for REST APIs",
        "coder: Create a FastAPI with auth",
        "tester: Write pytest tests",
        "security: Audit the code for vulnerabilities"
    )
    
    foreach ($t in $tasks) {
        $parts = $t -split ':'
        $agent = $parts[0]
        $prompt = $parts[1]
        
        Write-Host "`n[$agent] $prompt" -ForegroundColor Green
        $cmd = "python $NEWMETA -p $Provider --chat -m `"/agent $agent $prompt`""
        cmd /c $cmd
        
        Start-Sleep -Seconds 2
    }
    
    Write-Host "`n[Done] Chain complete!" -ForegroundColor Green
    exit 0
}

# Watch mode - monitor folder and auto-run
if ($Watch) {
    Write-Host "[Watch] Monitoring for changes..." -ForegroundColor Yellow
    Write-Host "  Add files to watch, auto-analyze them"
    Write-Host "  Press Ctrl+C to stop`n"
    
    while ($true) {
        # Check for new files in work folder
        $workDir = "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta\work"
        Get-ChildItem $workDir -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-1) } | ForEach-Object {
            Write-Host "[New File] $($_.Name)" -ForegroundColor Cyan
            $cmd = "python $NEWMETA -p $Provider --chat -m `"/agent security analyze $($_.FullName)`""
            cmd /c $cmd
        }
        Start-Sleep -Seconds 30
    }
}

# Default: Show help
Write-Host @"

🚀 NewMeta Auto-Runner

Usage:
  .\auto_run.ps1 -Task "<prompt>"        - Run single automated task
  .\auto_run.ps1 -Chain                  - Run agent chain
  .\auto_run.ps1 -Watch                   - Watch folder for auto-analysis
  .\auto_run.ps1 -Provider <name>         - Use specific provider

Examples:
  .\auto_run.ps1 -Task "create a python calculator"
  .\auto_run.ps1 -Task "analyze this code for bugs" -Provider ollama
  .\auto_run.ps1 -Chain
  .\auto_run.ps1 -Watch

Providers: openrouter (default), ollama, mephissa, openai, anthropic, gemini

"@ -ForegroundColor White