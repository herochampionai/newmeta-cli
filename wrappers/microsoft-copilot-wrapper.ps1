param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskParts
)

$ErrorActionPreference = 'Stop'
$appPath = "C:\Program Files (x86)\Microsoft\Copilot\Application\mscopilot.exe"
$task = ($TaskParts -join " ").Trim()

function Test-CopilotInstalled {
    return (Test-Path -LiteralPath $appPath)
}

function Get-ClipboardFiles {
    try {
        $items = @(Get-Clipboard -Format FileDropList -ErrorAction Stop | ForEach-Object { "$_" })
        return @($items | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
    } catch {
        return @()
    }
}

function Save-ClipboardImage {
    param([string]$OutputPath = "")

    $targetDir = Join-Path $env:TEMP 'NewMetaClipboard'
    if (-not (Test-Path -LiteralPath $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    $target = if ($OutputPath) { $OutputPath } else { Join-Path $targetDir ("clipboard-image-{0}.png" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) }
    try {
        Add-Type -AssemblyName System.Windows.Forms,System.Drawing
        $img = Get-Clipboard -Format Image -ErrorAction Stop
        if (-not $img) {
            return ""
        }
        $img.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
        if (Test-Path -LiteralPath $target) {
            return $target
        }
    } catch {}
    return ""
}

function Get-ClipboardPayload {
    $files = Get-ClipboardFiles
    $imagePath = Save-ClipboardImage
    $attachments = @()

    if ($imagePath) {
        $attachments += $imagePath
    }
    foreach ($item in $files) {
        if ($attachments -notcontains $item) {
            $attachments += $item
        }
    }

    [pscustomobject]@{
        Files = $files
        ImagePath = $imagePath
        Attachments = $attachments
    }
}

function Get-CopilotUri {
    param(
        [string]$Prompt,
        [string[]]$Attachments
    )

    $pairs = @()
    if (-not [string]::IsNullOrWhiteSpace($Prompt)) {
        $pairs += 'text=' + [System.Uri]::EscapeDataString($Prompt)
    }

    $cleanAttachments = @(
        $Attachments |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { [System.IO.Path]::GetFullPath($_) } |
            Select-Object -Unique
    )

    if ($cleanAttachments.Count -eq 1) {
        $pairs += 'file=' + [System.Uri]::EscapeDataString($cleanAttachments[0])
    } elseif ($cleanAttachments.Count -gt 1) {
        $pairs += 'files=' + [System.Uri]::EscapeDataString(($cleanAttachments -join ';'))
    }

    if ($pairs.Count -eq 0) {
        return ""
    }

    return 'ms-copilot://chat?' + ($pairs -join '&')
}

function Invoke-CopilotDeepLink {
    param(
        [string]$Prompt,
        [string[]]$Attachments
    )

    $uri = Get-CopilotUri -Prompt $Prompt -Attachments $Attachments
    if ([string]::IsNullOrWhiteSpace($uri)) {
        return $false
    }
    if ($uri.Length -gt 3200) {
        return $false
    }

    try {
        Start-Process $uri | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Focus-CopilotWindow {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NewMetaWin32 {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@ -ErrorAction SilentlyContinue

    $proc = $null
    for ($i = 0; $i -lt 8 -and -not $proc; $i++) {
        $proc = Get-Process -Name mscopilot -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'Copilot' } |
            Select-Object -First 1
        if (-not $proc) {
            Start-Sleep -Milliseconds 750
        }
    }

    if ($proc -and $proc.MainWindowHandle -ne 0) {
        [NewMetaWin32]::ShowWindowAsync($proc.MainWindowHandle, 9) | Out-Null
        Start-Sleep -Milliseconds 500
        [NewMetaWin32]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 700
    }

    $shell = New-Object -ComObject WScript.Shell
    foreach ($title in @('Copilot', 'Microsoft Copilot')) {
        if ($shell.AppActivate($title)) {
            Start-Sleep -Milliseconds 600
            $shell.SendKeys('^l')
            Start-Sleep -Milliseconds 150
            $shell.SendKeys('{ESC}')
            Start-Sleep -Milliseconds 150
            return $shell
        }
    }

    return $null
}

function Invoke-CopilotClipboardFallback {
    param(
        [string]$Prompt,
        [string[]]$Attachments,
        [bool]$HasNativeClipboardAttachments
    )

    try {
        Start-Process -FilePath $appPath | Out-Null
    } catch {
        Write-Host "[ERROR] Failed to launch Microsoft Copilot app: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }

    if ([string]::IsNullOrWhiteSpace($Prompt) -and -not $HasNativeClipboardAttachments) {
        Write-Host "[OK] Microsoft Copilot app launched." -ForegroundColor Green
        return
    }

    Start-Sleep -Seconds 4
    $shell = Focus-CopilotWindow
    if (-not $shell) {
        Write-Host "[WARN] Microsoft Copilot app launched, but the window could not be focused automatically." -ForegroundColor Yellow
        return
    }

    $sent = $false
    try {
        if ($HasNativeClipboardAttachments) {
            $shell.SendKeys('^v')
            Start-Sleep -Milliseconds 900
        }

        if (-not [string]::IsNullOrWhiteSpace($Prompt)) {
            Set-Clipboard -Value $Prompt
            Start-Sleep -Milliseconds 150
            $shell.SendKeys('^v')
            Start-Sleep -Milliseconds 250
        }

        $shell.SendKeys('~')
        $sent = $true
    } catch {
        $sent = $false
    }

    if ($sent) {
        Write-Host "[OK] Microsoft Copilot app launched and task submitted via clipboard fallback." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Microsoft Copilot app launched, but automatic submission failed." -ForegroundColor Yellow
    }
}

if (-not (Test-CopilotInstalled)) {
    Write-Host "[ERROR] Microsoft Copilot app not found at $appPath" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($task)) {
    Start-Process -FilePath $appPath | Out-Null
    Write-Host "[OK] Microsoft Copilot app launched." -ForegroundColor Green
    exit 0
}

$clipboardPayload = Get-ClipboardPayload
$nativeClipboardAttachments = ($clipboardPayload.Files.Count -gt 0) -or (-not [string]::IsNullOrWhiteSpace($clipboardPayload.ImagePath))
$deepLinkAttachments = @($clipboardPayload.Attachments)

if (Invoke-CopilotDeepLink -Prompt $task -Attachments $deepLinkAttachments) {
    if ($deepLinkAttachments.Count -gt 0) {
        Write-Host "[OK] Microsoft Copilot opened via deep link with task and attachment handoff." -ForegroundColor Green
    } else {
        Write-Host "[OK] Microsoft Copilot opened via deep link with task handoff." -ForegroundColor Green
    }
    exit 0
}

Invoke-CopilotClipboardFallback -Prompt $task -Attachments $deepLinkAttachments -HasNativeClipboardAttachments:$nativeClipboardAttachments
