# ============================================================================
# VcanTrade AI — Teacher-Only Installer (Windows)
#
# Same as install.ps1 but locks the bot into Teacher Mode.
# The Autonomous button on the dashboard is greyed out and cannot be clicked.
# Use this for installs where the human always approves every trade.
#
# Usage (PowerShell, copy this whole line):
#   iwr -useb https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install-teacher.ps1 | iex
# ============================================================================

$ErrorActionPreference = "Stop"

# Edit these two lines once you push the repo to GitHub:
$RepoUrl     = "https://github.com/Vijindran79/vcantrade.com.git"
$InstallRoot = Join-Path $HOME "VcanTrade"

# Run the standard installer first.
$mainInstaller = "https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.ps1"
Invoke-WebRequest -UseBasicParsing -Uri $mainInstaller | Invoke-Expression

# Force Teacher-only lock in .env so the dashboard can never flip to Autonomous.
$envPath = Join-Path $InstallRoot ".env"
if (Test-Path $envPath) {
    $content = Get-Content $envPath -Raw
    if ($content -match "(?m)^TEACHER_ONLY_LOCK=") {
        $content = [regex]::Replace($content, "(?m)^TEACHER_ONLY_LOCK=.*", "TEACHER_ONLY_LOCK=True")
    } else {
        $content = $content.TrimEnd() + "`r`nTEACHER_ONLY_LOCK=True`r`n"
    }
    Set-Content -Path $envPath -Value $content -Encoding UTF8
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Magenta
    Write-Host "  TEACHER MODE LOCKED" -ForegroundColor Magenta
    Write-Host "============================================================" -ForegroundColor Magenta
    Write-Host "  The bot will analyze and suggest only." -ForegroundColor White
    Write-Host "  The Autonomous button is greyed out — you click the broker yourself." -ForegroundColor White
    Write-Host ""
} else {
    Write-Host "WARNING: .env not found at $envPath. Open it later and set TEACHER_ONLY_LOCK=True manually." -ForegroundColor Yellow
}
