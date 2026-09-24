<#
    Prove the whole TradingView -> middleware -> broker path on this machine,
    with no Tradovate credentials.

    * starts the middleware in DRY_RUN (app/sim_broker.py replaces the real
      client, so no order can leave the box)
    * waits for /health
    * drives scripts/e2e_signal_test.py against it, with the shared secret in
      the URL exactly the way a TradingView alert must carry it
    * prints the server log and exits with the harness' verdict

        powershell -File scripts\run_dry_run_e2e.ps1
        powershell -File scripts\run_dry_run_e2e.ps1 -KeepState   # keep risk/drawdown history
#>
param(
    [switch]$KeepState,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Get-EnvValue([string]$Name) {
    $hit = Select-String -Path .env -Pattern "^\s*$Name\s*=\s*(.*)$" | Select-Object -First 1
    if (-not $hit) { return '' }
    # .env values carry inline comments and sometimes quotes; neither belongs in
    # the value (a trailing comment otherwise breaks [int]::TryParse).
    $value = ($hit.Matches[0].Groups[1].Value -split '\s+#')[0]
    return $value.Trim().Trim('"').Trim("'")
}

if (-not (Test-Path .env)) {
    throw '.env not found. Copy .env.example to .env first (the DRY_RUN block at the top is enough).'
}

$key = Get-EnvValue 'WEBHOOK_API_KEY'
if (-not $key) { throw 'WEBHOOK_API_KEY is missing from .env' }
if ((Get-EnvValue 'DRY_RUN') -notmatch '^(?i)true$') {
    throw 'this runner expects DRY_RUN=true in .env; it is meant to be un-runnable against a broker'
}
if (-not $Port) {
    $Port = 0
    [void][int]::TryParse((Get-EnvValue 'PORT'), [ref]$Port)
    if (-not $Port) { $Port = 8080 }
}

New-Item -ItemType Directory -Force -Path state | Out-Null

# A silent local collision here is expensive to debug: on Windows an unrelated
# service bound to 0.0.0.0 or :: can answer "404 Not Found" for /health and look
# exactly like a broken middleware.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $proc = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    throw "port $Port is already in use by '$proc' (pid $($busy.OwningProcess)). Set a free PORT in .env or pass -Port."
}

# From here on the server's JSON logs arrive on stderr; they must be captured,
# not treated as fatal errors.
$ErrorActionPreference = 'Continue'
if (-not $KeepState) {
    foreach ($f in 'state\risk_state.json', 'state\sim_orders.json') {
        if (Test-Path $f) { Remove-Item $f -Force; Write-Host "cleared $f" }
    }
}

Write-Host "starting DRY_RUN middleware on 127.0.0.1:$Port ..."
$job = Start-Job -ScriptBlock {
    param($Dir, $Port)
    Set-Location $Dir
    $env:PORT = "$Port"
    python -m app.main
} -ArgumentList (Get-Location).Path, $Port

$base = "http://127.0.0.1:$Port"
Write-Host "waiting for $base/health ..."
$up = $false
$lastErr = ''
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        if ((Invoke-WebRequest -UseBasicParsing "$base/health" -TimeoutSec 2).StatusCode -eq 200) {
            $up = $true
            break
        }
    } catch {
        $lastErr = $_.Exception.Message
    }
}

$exitCode = 1
$serverLog = ''
try {
    if (-not $up) {
        Write-Host "FATAL: $base/health never answered 200. Last error: $lastErr"
        $exitCode = 1
    } else {
        Write-Host ''
        python scripts/e2e_signal_test.py --url $base --key $key --url-key
        $exitCode = $LASTEXITCODE
    }
} finally {
    # Native stderr from the uvicorn child arrives as error records; capture it
    # as text so a startup traceback is never swallowed.
    $serverLog = Receive-Job $job -ErrorAction SilentlyContinue 2>&1 | Out-String
    Write-Host ''
    Write-Host '--- server log (tail) ---'
    ($serverLog -split "`r?`n" | Select-Object -Last 30) | ForEach-Object { Write-Host $_ }
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}
exit $exitCode
