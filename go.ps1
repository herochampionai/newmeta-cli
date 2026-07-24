# Quick launcher - just run "nm" or "newmeta" from anywhere
# This adds a global alias for NewMeta

$ALIAS_FILE = "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps\nm.cmd"

$content = '@echo off
python "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta\cli.py" -p openrouter --chat %*'

[System.IO.File]::WriteAllText($ALIAS_FILE, $content, [System.Text.Encoding]::ASCII)

Write-Host "✅ Created 'nm' command!" -ForegroundColor Green
Write-Host "  Now type 'nm' anywhere in terminal to launch NewMeta"
Write-Host "  Or use 'newmeta' as before"