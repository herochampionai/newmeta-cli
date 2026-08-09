param(
  [string]$Tool,
  [string]$Args
)

$host.UI.RawUI.WindowTitle = "◆ $Tool"

function Write-ProgressBar {
  param([int]$Current, [int]$Total, [string]$Label, [string]$Status = "running")
  $filled = "■"
  $empty = "□"
  $barLen = 12
  $ratio = if ($Total -gt 0) { $Current / $Total } else { 0 }
  $fc = [Math]::Min([Math]::Round($ratio * $barLen), $barLen)
  $bar = $filled * $fc + $empty * ([Math]::Max($barLen - $fc, 0))
  $pct = [Math]::Min([Math]::Round($ratio * 100), 100)

  $sym = switch ($Status) {
    "done" { "✓" }
    "failed" { "✗" }
    "running" { "●" }
    default { "◆" }
  }

  Write-Host "  $sym [$bar] $pct%  $Label" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║        $Tool  —  Agent Session               ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""

function Invoke-Tool {
  param([string]$Name, [scriptblock]$Block)
  Write-Host "  ● [$Name] initializing..." -ForegroundColor Yellow
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    & $Block
    $sw.Stop()
    Write-Host "  ✓ [$Name] completed in $($sw.Elapsed.TotalSeconds.ToString('0.0'))s" -ForegroundColor Green
  } catch {
    $sw.Stop()
    Write-Host "  ✗ [$Name] failed after $($sw.Elapsed.TotalSeconds.ToString('0.0'))s: $_" -ForegroundColor Red
  }
}

switch ($Tool.ToLower()) {
  "crush" {
    Invoke-Tool "Launch Crush" { crush run $Args }
  }
  "pi" {
    Invoke-Tool "Launch Pi" { pi $Args }
  }
  default {
    Write-Host "  ✗ Unknown tool: $Tool" -ForegroundColor Red
  }
}
