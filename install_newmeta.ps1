$ErrorActionPreference = "Stop"

$NEWMETA_PATH = "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   NewMeta - PATH Installation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verify NewMeta exists
if (!(Test-Path "$NEWMETA_PATH\cli.py")) {
    Write-Host "ERROR: NewMeta not found at $NEWMETA_PATH" -ForegroundColor Red
    exit 1
}

# Add to User PATH (permanent - works in CMD, PowerShell, Git Bash, etc.)
$currentPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")

if ($currentPath -notlike "*NewMeta*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$currentPath;$NEWMETA_PATH", "User")
    Write-Host "[OK] Added to User PATH" -ForegroundColor Green
} else {
    Write-Host "[OK] Already in PATH" -ForegroundColor Green
}

# Also add .cmd version for extra compatibility
$cmdFile = "$NEWMETA_PATH\newmeta.cmd"
$batContent = "@echo off`npython `"%~dp0cli.py`" %*"
Set-Content -Path $cmdFile -Value $batContent -Encoding ASCII

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   INSTALLATION COMPLETE!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "You can now type in ANY terminal:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  newmeta --chat              (Start interactive chat)"
Write-Host "  newmeta \"Hello world\"       (Quick question)"
Write-Host "  newmeta --version           (Show version)"
Write-Host "  newmeta --list-providers    (Show providers)"
Write-Host ""
Write-Host "Supported: CMD, PowerShell, Git Bash, WSL" -ForegroundColor Gray
Write-Host ""
Write-Host "IMPORTANT: Open a NEW terminal window to use!" -ForegroundColor Yellow
Write-Host ""
Write-Host "If still not working, restart your computer." -ForegroundColor Gray