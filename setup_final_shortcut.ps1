Add-Type -AssemblyName System.Drawing

# --- Create custom Lion Icon ---
$iconPath = "C:\Users\vijin\Desktop\vcantrade_lion.ico"
$bmp = New-Object System.Drawing.Bitmap 256, 256
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

# Dark blue gradient background
$bgRect = New-Object System.Drawing.Rectangle 0, 0, 256, 256
$bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $bgRect,
    [System.Drawing.Color]::FromArgb(255, 8, 15, 35),
    [System.Drawing.Color]::FromArgb(255, 20, 50, 100),
    45.0
)
$g.FillRectangle($bgBrush, $bgRect)

# Gold outer ring
$ringPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 255, 200, 50), 6)
$g.DrawEllipse($ringPen, 40, 40, 176, 176)

# Lion mane (dark gold)
$maneBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 180, 120, 20))
$g.FillEllipse($maneBrush, 70, 70, 116, 116)

# Lion face (bright gold)
$faceBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 255, 200, 60))
$g.FillEllipse($faceBrush, 92, 92, 72, 72)

# Eyes
$eyeBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 20, 10, 5))
$g.FillEllipse($eyeBrush, 110, 115, 10, 12)
$g.FillEllipse($eyeBrush, 136, 115, 10, 12)

# Eye highlights
$highlightBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
$g.FillEllipse($highlightBrush, 113, 117, 3, 3)
$g.FillEllipse($highlightBrush, 139, 117, 3, 3)

# Nose
$noseBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 60, 20, 10))
$g.FillEllipse($noseBrush, 122, 140, 12, 8)

# Mouth
$mouthPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 60, 20, 10), 2.5)
$g.DrawArc($mouthPen, 118, 145, 20, 12, 0, 180)

$g.Dispose()

# Save as ICO
$hicon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hicon)
$fs = New-Object System.IO.FileStream($iconPath, "Create")
$icon.Save($fs)
$fs.Close()
$bmp.Dispose()

Write-Host "Icon created: $iconPath"

# --- Remove old bot shortcuts from Desktop ---
$desktopPath = [Environment]::GetFolderPath("Desktop")
$oldDesktopItems = @("MiroTrader.lnk")
foreach ($item in $oldDesktopItems) {
    $path = Join-Path $desktopPath $item
    if (Test-Path $path) {
        Remove-Item $path -Force
        Write-Host "Removed desktop: $item"
    }
}

# --- Remove old bot items from Startup folder ---
$startupPath = [Environment]::GetFolderPath("Startup")
$startupItems = Get-ChildItem -Path $startupPath -ErrorAction SilentlyContinue
$botKeywords = @("VcanTrade", "Lion", "MiroTrader", "hermes", "lion_watchdog", "run_lion_bot",
                  "Start_VcanTrade", "BackupBeforeCleanup", "LionDebug", "run_as_admin",
                  "capture_ssh", "check_hermes", "configure_hermes", "create_workaround",
                  "fix_hermes", "fix_ssh_keyauth", "test_ssh_direct", "verify_hermes",
                  "watch_hermes", "VERIFY_SETUP", "FINAL_VERIFICATION", "QUICK_REFERENCE",
                  "SSH_KEY_ONLY")

foreach ($item in $startupItems) {
    $shouldRemove = $false
    foreach ($keyword in $botKeywords) {
        if ($item.Name -like "*$keyword*") {
            $shouldRemove = $true
            break
        }
    }
    if ($shouldRemove) {
        try {
            Remove-Item $item.FullName -Force -Recurse -ErrorAction Stop
            Write-Host "Removed startup: $($item.Name)"
        } catch {
            Write-Host "Could not remove: $($item.Name) - $_"
        }
    }
}

# --- Create the ONE final shortcut on Desktop ---
$shortcutPath = Join-Path $desktopPath "VcanTrade AI.lnk"
if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force }

$WshShell = New-Object -ComObject WScript.Shell
$shortcut = $WshShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "C:\Users\vijin\vcantrade.com-3\launch.bat"
$shortcut.WorkingDirectory = "C:\Users\vijin\vcantrade.com-3"
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = "VcanTrade AI - The Lion (Final Launcher)"
$shortcut.Save()

Write-Host ""
Write-Host "============================================"
Write-Host "  CLEANUP COMPLETE"
Write-Host "============================================"
Write-Host "Final shortcut: $shortcutPath"
Write-Host "Icon: $iconPath"
Write-Host "============================================"
