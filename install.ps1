# ============================================================================
# VcanTrade AI — One-Line Installer (Windows)
#
# Usage (PowerShell, copy this whole line):
#   iwr -useb https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.ps1 | iex
#
# What it does:
#   1. Checks for Python 3.11 (installs if missing)
#   2. Clones / updates the bot into  $HOME\VcanTrade
#   3. Creates a virtual environment and installs all dependencies
#   4. Installs Playwright Chromium (for TradingView Desktop control)
#   5. Drops a "VcanTrade" shortcut on the Desktop
#
# After install:
#   * Open MetaTrader 5 (if your brother is on MT5) OR TradingView Desktop
#   * Double-click the Desktop shortcut "VcanTrade"
# ============================================================================

$ErrorActionPreference = "Stop"

# Edit these two lines once you push the repo to GitHub:
$RepoUrl     = "https://github.com/Vijindran79/vcantrade.com.git"
$InstallRoot = Join-Path $HOME "VcanTrade"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  VcanTrade AI — Windows Installer" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Python 3.11 check -----------------------------------------------------
function Test-Python311 {
    try {
        $v = & py -3.11 --version 2>&1
        return $LASTEXITCODE -eq 0 -and $v -match "3\.11"
    } catch { return $false }
}

if (-not (Test-Python311)) {
    Write-Host "[1/5] Python 3.11 not found — installing via winget..." -ForegroundColor Yellow
    try {
        winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    } catch {
        Write-Host "winget failed. Please install Python 3.11 from https://www.python.org/downloads/release/python-3119/ then re-run this installer." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[1/5] Python 3.11 found." -ForegroundColor Green
}

# --- 2. Git check -------------------------------------------------------------
function Test-Git {
    try { (& git --version 2>&1) -match "git version" } catch { return $false }
}
if (-not (Test-Git)) {
    Write-Host "[2/5] Git not found — installing via winget..." -ForegroundColor Yellow
    try {
        winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
        # winget puts git on PATH for new shells; refresh this session
        $env:Path += ";C:\Program Files\Git\cmd"
    } catch {
        Write-Host "Could not install Git automatically. Install Git from https://git-scm.com/download/win then re-run." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[2/5] Git found." -ForegroundColor Green
}

# --- 3. Clone / update repo ---------------------------------------------------
Write-Host "[3/5] Downloading code into $InstallRoot ..." -ForegroundColor Cyan
if (Test-Path (Join-Path $InstallRoot ".git")) {
    Push-Location $InstallRoot
    git fetch --all --quiet
    git reset --hard origin/main --quiet
    Pop-Location
    Write-Host "    Updated existing copy." -ForegroundColor Green
} else {
    if (Test-Path $InstallRoot) {
        Remove-Item -Recurse -Force $InstallRoot
    }
    git clone --depth 1 $RepoUrl $InstallRoot
    Write-Host "    Cloned fresh copy." -ForegroundColor Green
}

# --- 4. Virtual env + dependencies -------------------------------------------
Write-Host "[4/5] Installing Python packages (this can take a few minutes)..." -ForegroundColor Cyan
Push-Location $InstallRoot

if (-not (Test-Path ".venv")) {
    & py -3.11 -m venv .venv
}

$venvPython = Join-Path $InstallRoot ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet

Write-Host "    Installing Playwright browser (Chromium)..." -ForegroundColor Cyan
& $venvPython -m playwright install chromium

# Copy .env.example to .env if .env doesn't exist
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "    Created .env from template." -ForegroundColor Green
}

Pop-Location

# --- 5. Desktop shortcut ------------------------------------------------------
Write-Host "[5/5] Creating Desktop shortcut..." -ForegroundColor Cyan
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "VcanTrade.lnk"
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
# Point at launch.bat so ONE click opens the browser on port 9222 and starts
# the bot. start.bat alone would skip the browser, breaking the chain.
$Shortcut.TargetPath  = Join-Path $InstallRoot "launch.bat"
$Shortcut.WorkingDirectory = $InstallRoot
$Shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,77"
$Shortcut.Description = "VcanTrade AI — opens browser on port 9222 and starts the bot"
$Shortcut.Save()

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  DONE!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Open MetaTrader 5 OR TradingView Desktop" -ForegroundColor White
Write-Host "  2. Make sure Ollama is running (run:  ollama serve  in a separate terminal)" -ForegroundColor White
Write-Host "  3. Double-click the 'VcanTrade' icon on your Desktop" -ForegroundColor White
Write-Host ""
Write-Host "Installed at: $InstallRoot" -ForegroundColor Gray
Write-Host ""
