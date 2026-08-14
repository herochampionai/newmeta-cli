# ========================================
# ULTIMATE TERMINAL SHOW
# ========================================
# Needs external tools NOT part of the self-contained Zouzou effects
# (matrix_rain.py etc. at ~/.claude/zouzou/) -- this script uses real
# third-party installs instead: terminaltexteffects (pip), a MatrixRain
# winget package, ruclouds.exe (downloaded from GitHub releases), and
# ascii.live (a third-party remote server streamed via curl).
#
# INSTALLATION (run once, as Administrator):
#   pip install terminaltexteffects
#   winget install --id relmer.MatrixRain
#   winget install --id Microsoft.WindowsTerminal
#   winget install --id curl.curl
#   Download ruclouds.exe from https://github.com/AndyFerns/ruclouds/releases
#   and place it at the path in $CloudsPath below (edit it first).

# ========================================
# CONFIGURATION
# ========================================
$MatrixPath = "MatrixRain.exe"                              # must be on PATH, or give a full path
$CloudsPath = "C:\Users\youha\ruclouds.exe"                  # <-- edit if you place it elsewhere
$TTECmd     = "tte"                                          # assumes tte is on PATH after pip install

# ========================================
# HELPER FUNCTIONS
# ========================================
function Wait-Enter {
    Write-Host "Press Enter to continue..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    Clear-Host
}

function Show-Title {
    param([string]$Text, [ConsoleColor]$Color = [ConsoleColor]::Cyan)
    Clear-Host
    Write-Host ""
    Write-Host $Text -ForegroundColor $Color -BackgroundColor Black
    Write-Host ""
}

function Start-App-Wait {
    param([string]$Path, [string[]]$ArgumentList = @(), [ConsoleColor]$Color = [ConsoleColor]::White)
    Clear-Host
    Write-Host "Launching: $Path" -ForegroundColor $Color
    try {
        $proc = Start-Process -FilePath $Path -ArgumentList $ArgumentList -WindowStyle Maximized -PassThru
    } catch {
        Write-Host "Failed to start: $Path" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Wait-Enter
        return
    }
    Wait-Enter
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
}

function Start-TTE-Effect {
    param([string]$Text, [string]$Effect, [string[]]$Args = @(), [ConsoleColor]$Color = [ConsoleColor]::Magenta)
    Clear-Host
    Write-Host "TTE EFFECT: $Effect" -ForegroundColor $Color
    Write-Host "Text: $Text" -ForegroundColor Gray
    $allArgs = @("-NoExit", "-Command", "echo '$Text' | $TTECmd $Effect $Args")
    Start-Process powershell -ArgumentList $allArgs -WindowStyle Maximized
    Wait-Enter
}

function Start-Curl-Ascii {
    param([string]$Url, [ConsoleColor]$Color = [ConsoleColor]::Yellow)
    Clear-Host
    Write-Host "ASCII SHOW: $Url" -ForegroundColor $Color
    $allArgs = @("-NoExit", "-Command", "curl $Url")
    Start-Process powershell -ArgumentList $allArgs -WindowStyle Maximized
    Wait-Enter
}

# ========================================
# THE SHOW
# ========================================
Clear-Host
Write-Host "ULTIMATE TERMINAL SHOW" -ForegroundColor Cyan
Write-Host "A cinematic terminal experience" -ForegroundColor Gray
Write-Host ""
Wait-Enter

Show-Title "ACT I -- MATRIX" -Color Green
Start-App-Wait -Path $MatrixPath -Color Green

Show-Title "ACT II -- CLOUDS" -Color Magenta
Start-App-Wait -Path $CloudsPath -ArgumentList @("--palette", "midnight") -Color Magenta

Show-Title "ACT III -- TEXT EFFECTS" -Color Red
Start-TTE-Effect -Text "MELISSA"  -Effect "fireworks" -Args @("--speed", "100") -Color Red
Start-TTE-Effect -Text "FATALITY" -Effect "decrypt"   -Args @("--speed", "50")  -Color Cyan
Start-TTE-Effect -Text "GLITCH"   -Effect "glitch"    -Args @("--intensity", "15") -Color Yellow

Show-Title "ACT IV -- STAR WARS" -Color Yellow
Start-Curl-Ascii -Url "ascii.live/starwars" -Color Yellow

Show-Title "FINALE -- EVERYTHING AT ONCE" -Color Red
$null = Start-Process -FilePath $MatrixPath -WindowStyle Maximized
$null = Start-Process -FilePath $CloudsPath -ArgumentList @("--palette", "neon") -WindowStyle Maximized
$fireArgs = @("-NoExit", "-Command", "echo 'TERMINAL FATALITY' | $TTECmd fireworks --speed 200 --colors red,orange,yellow,white")
$null = Start-Process powershell -ArgumentList $fireArgs -WindowStyle Maximized
$matrixAsciiArgs = @("-NoExit", "-Command", "curl ascii.live/matrix")
$null = Start-Process powershell -ArgumentList $matrixAsciiArgs -WindowStyle Maximized

Write-Host ""
Write-Host "Finale running... enjoy the chaos." -ForegroundColor Green
Write-Host "Close windows manually when done, then press Enter here." -ForegroundColor Gray
Read-Host "Press Enter to exit"
Clear-Host
Write-Host "SHOW COMPLETE." -ForegroundColor Green
