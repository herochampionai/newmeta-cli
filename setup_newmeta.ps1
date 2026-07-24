$ErrorActionPreference = "Stop"
$NEWMETA_PATH = "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta"

# Check if NewMeta folder exists
if (!(Test-Path $NEWMETA_PATH)) {
    Write-Host "❌ NewMeta folder not found at: $NEWMETA_PATH" -ForegroundColor Red
    exit 1
}

# Add to User PATH (permanent)
$currentPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*NewMeta*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$currentPath;$NEWMETA_PATH", "User")
    Write-Host "✅ NewMeta added to PATH!" -ForegroundColor Green
} else {
    Write-Host "✅ NewMeta already in PATH" -ForegroundColor Green
}

Write-Host ""
Write-Host "🎉 SUCCESS! You can now use NewMeta from any terminal:" -ForegroundColor Cyan
Write-Host ""
Write-Host "   newmeta --chat              → Start chat"
Write-Host "   newmeta 'your question'     → Ask anything"
Write-Host "   newmeta --status            → Check models"
Write-Host "   newmeta --set-key openai YOUR_KEY → Set API key"
Write-Host ""
Write-Host "Open a NEW terminal and try it!"