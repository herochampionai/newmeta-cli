$ALIAS_FILE = "$env:USERPROFILE\AppData\Local\Microsoft\WindowsApps\nm-agents.cmd"

$content = '@echo off
python "C:\Users\youha\OneDrive\Desktop\Codes\pika poke\NewMeta\cli.py" --list-agents'

[System.IO.File]::WriteAllText($ALIAS_FILE, $content, [System.Text.Encoding]::ASCII)

Write-Host "✅ Created 'nm-agents' command!" -ForegroundColor Green
Write-Host "  Now type 'nm-agents' in any terminal to see NewMeta agents"