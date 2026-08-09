param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskArgs
)

$ErrorActionPreference = 'SilentlyContinue'
$taskText = ($TaskArgs -join ' ').Trim()

function Write-Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Bad($msg) { Write-Host $msg -ForegroundColor Red }

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    Write-Bad '[Plandex] WSL is not installed. Plandex only works correctly on Windows through WSL.'
    Write-Host 'Install Ubuntu for WSL, then install Plandex inside Ubuntu.'
    exit 1
}

$distrosRaw = & wsl.exe -l -q 2>$null
$distros = @($distrosRaw | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ -and $_ -notmatch '^docker-desktop' })
if (-not $distros -or $distros.Count -eq 0) {
    Write-Bad '[Plandex] No normal WSL Linux distro found. Current WSL only has docker-desktop.'
    Write-Host 'Install one first:'
    Write-Host '  wsl --install -d Ubuntu'
    Write-Host 'Then in Ubuntu:'
    Write-Host '  curl -sL https://plandex.ai/install.sh | bash'
    exit 1
}

$distro = $distros[0]
$plandexPath = (& wsl.exe -d $distro -- sh -lc 'command -v plandex || command -v pdx || true' 2>$null | Select-Object -First 1).Trim()
if (-not $plandexPath) {
    Write-Bad "[Plandex] WSL distro '$distro' exists, but Plandex is not installed inside it."
    Write-Host 'Install it inside WSL:'
    Write-Host '  curl -sL https://plandex.ai/install.sh | bash'
    exit 1
}

$winPaths = [regex]::Matches($taskText, '"([A-Za-z]:\\[^"\r\n]+)"|([A-Za-z]:\\\S+)') | ForEach-Object {
    if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value }
}
$projectPath = if ($winPaths.Count -gt 0) { $winPaths[0] } else { (Get-Location).Path }
if (Test-Path -LiteralPath $projectPath -PathType Leaf) { $projectPath = Split-Path -LiteralPath $projectPath -Parent }

$wslProjectPath = (& wsl.exe -d $distro -- wslpath -a "$projectPath" 2>$null | Select-Object -First 1).Trim()
if (-not $wslProjectPath) {
    Write-Bad "[Plandex] Could not map Windows path to WSL: $projectPath"
    exit 1
}

Write-Info "[Plandex] distro: $distro"
Write-Info "[Plandex] project: $projectPath"
& wsl.exe -d $distro -- sh -lc "cd '$($wslProjectPath -replace '''', '''\''''')' && '$($plandexPath -replace '''', '''\''''')'"
exit $LASTEXITCODE