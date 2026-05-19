# ============================================================================
# VcanTrade AI - Recreate Desktop shortcut
#
# Run this once if the Desktop icon is missing or broken:
#   Right-click  -> Run with PowerShell
# Or in PowerShell:
#   powershell -ExecutionPolicy Bypass -File create-desktop-shortcut.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Target    = Join-Path $ScriptDir "launch.bat"

if (-not (Test-Path $Target)) {
    Write-Host "ERROR: launch.bat not found in $ScriptDir" -ForegroundColor Red
    Write-Host "Make sure this script lives next to launch.bat (same folder)." -ForegroundColor Yellow
    exit 1
}

$DesktopPath  = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "VcanTrade.lnk"
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath       = $Target
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.IconLocation     = "$env:SystemRoot\System32\imageres.dll,77"
$Shortcut.Description      = "VcanTrade AI - opens browser on port 9222 and starts the bot"
$Shortcut.Save()

Write-Host ""
Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $ShortcutPath" -ForegroundColor White
Write-Host ""
Write-Host "Double-click 'VcanTrade' on your Desktop to launch." -ForegroundColor White
