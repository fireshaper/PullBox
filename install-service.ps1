# install-service.ps1
# Install (or reconfigure) PullBox as a Windows service via NSSM.
#
# Run ELEVATED on the server:
#   powershell -ExecutionPolicy Bypass -File .\install-service.ps1 -Root D:\PullBox
#
# Re-running is safe: an existing service is stopped, reconfigured, and restarted.

[CmdletBinding()]
param(
    # Deploy root — the folder containing `backend\` and `config\`.
    [Parameter(Mandatory = $true)]
    [string]$Root,

    [string]$Nssm        = 'E:\Downloads\nssm-2.24\win64\nssm.exe',
    [string]$ServiceName = 'PullBox',
    [int]   $Port        = 8585,
    [string]$ListenHost  = '0.0.0.0',

    # Run the service as a specific account instead of LocalSystem. Required if
    # your library or download-client paths live on a network share.
    [string]$User,
    [string]$Password
)

$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── 0. Must be elevated ───────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail 'Run this from an elevated PowerShell (Run as Administrator).' }

# ── 1. Resolve and validate paths ─────────────────────────────────────────────
$Root = (Resolve-Path -LiteralPath $Root).Path
$Backend = Join-Path $Root 'backend'
$Python  = Join-Path $Backend '.venv\Scripts\python.exe'
$Config  = Join-Path $Root 'config\config.yaml'
$LogDir  = Join-Path $Root 'config\logs'

if (-not (Test-Path -LiteralPath $Nssm))    { Fail "nssm.exe not found at: $Nssm  (pass -Nssm <path>)" }
if (-not (Test-Path -LiteralPath $Backend)) { Fail "No 'backend' folder under: $Root" }
if (-not (Test-Path -LiteralPath $Python))  { Fail "No venv python at: $Python`n       Run 'uv sync' in $Backend first." }
if (-not (Test-Path -LiteralPath $Config))  { Fail "No config.yaml at: $Config" }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ── 2. Guard against an Anaconda-based venv ───────────────────────────────────
# A venv created from Anaconda's python hangs aiosqlite's async startup: the app
# binds the port but never finishes lifespan, so the service looks "running" and
# serves nothing. Catch it here rather than debugging a silent hang later.
$pyvenvCfg = Join-Path $Backend '.venv\pyvenv.cfg'
if (Test-Path -LiteralPath $pyvenvCfg) {
    $venvBase = (Select-String -LiteralPath $pyvenvCfg -Pattern '^\s*home\s*=\s*(.+)$').Matches.Groups[1].Value.Trim()
    if ($venvBase -match 'anaconda|miniconda|conda') {
        Fail ("The venv was built from a conda interpreter ($venvBase).`n" +
              "       Rebuild it on uv-managed CPython:`n" +
              "         cd $Backend; Remove-Item -Recurse -Force .venv; uv python install 3.12; uv sync")
    }
    Write-Host "    venv base interpreter: $venvBase" -ForegroundColor DarkGray
}

# ── 3. Remove any existing service so settings never merge with stale ones ────
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "==> Existing '$ServiceName' service found - stopping and removing ..." -ForegroundColor Cyan
    & $Nssm stop $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 2
    & $Nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 2
}

# ── 4. Install ────────────────────────────────────────────────────────────────
Write-Host "==> Installing service '$ServiceName' ..." -ForegroundColor Cyan
# Bypass uvicorn.exe and `uv run`: a service inherits the SYSTEM PATH, and any
# resolution through PATH is a chance to pick up the wrong interpreter. The venv
# python by absolute path is unambiguous.
$appArgs = "-m uvicorn pullbox.main:app --host $ListenHost --port $Port"
& $Nssm install $ServiceName $Python $appArgs
if ($LASTEXITCODE -ne 0) { Fail "nssm install failed (exit $LASTEXITCODE)" }

& $Nssm set $ServiceName AppDirectory  $Backend           | Out-Null
& $Nssm set $ServiceName DisplayName   'PullBox'          | Out-Null
& $Nssm set $ServiceName Description   'PullBox comic book acquisition manager' | Out-Null
& $Nssm set $ServiceName Start         SERVICE_AUTO_START | Out-Null

# Point the app at the config explicitly so it never depends on cwd or on the
# relative-path walk in config.py.
& $Nssm set $ServiceName AppEnvironmentExtra "PULLBOX_CONFIG_FILE=$Config" | Out-Null

# ── 5. Graceful shutdown ──────────────────────────────────────────────────────
# Try CTRL+C first and give uvicorn 15s: the lifespan shutdown is what closes the
# SQLite engine (flushing the WAL) and stops APScheduler. Skip straight past
# WM_CLOSE/thread methods, which do nothing for a console app, to a hard kill.
& $Nssm set $ServiceName AppStopMethodConsole 15000 | Out-Null
& $Nssm set $ServiceName AppStopMethodWindow  0     | Out-Null
& $Nssm set $ServiceName AppStopMethodThreads 0     | Out-Null
& $Nssm set $ServiceName AppKillProcessTree   1     | Out-Null

# ── 6. Restart policy ─────────────────────────────────────────────────────────
# Restart on any unexpected exit, but wait 5s and treat anything that dies within
# 10s as a crash-loop so a bad config doesn't spin the CPU restarting forever.
& $Nssm set $ServiceName AppExit Default Restart | Out-Null
& $Nssm set $ServiceName AppRestartDelay 5000    | Out-Null
& $Nssm set $ServiceName AppThrottle     10000   | Out-Null

# ── 7. Capture stdout/stderr ──────────────────────────────────────────────────
# The app's own rotating logs (config\logs\pullbox.log) only start once lifespan
# reaches configure_logging. Anything that kills the process before that - an
# import error, a bad config.yaml, a port already in use - only ever appears on
# stderr, so capture it or those failures are invisible.
$stdout = Join-Path $LogDir 'service-stdout.log'
$stderr = Join-Path $LogDir 'service-stderr.log'
& $Nssm set $ServiceName AppStdout $stdout | Out-Null
& $Nssm set $ServiceName AppStderr $stderr | Out-Null
& $Nssm set $ServiceName AppStdoutCreationDisposition 4 | Out-Null   # append
& $Nssm set $ServiceName AppStderrCreationDisposition 4 | Out-Null
& $Nssm set $ServiceName AppRotateFiles   1       | Out-Null
& $Nssm set $ServiceName AppRotateOnline  1       | Out-Null
& $Nssm set $ServiceName AppRotateBytes   5242880 | Out-Null

# ── 8. Optional service account ───────────────────────────────────────────────
if ($User) {
    Write-Host "==> Setting service account to $User ..." -ForegroundColor Cyan
    & $Nssm set $ServiceName ObjectName $User $Password | Out-Null
}

# ── 9. Start ──────────────────────────────────────────────────────────────────
Write-Host "==> Starting ..." -ForegroundColor Cyan
& $Nssm start $ServiceName
Start-Sleep -Seconds 5

$svc = Get-Service -Name $ServiceName
Write-Host ""
Write-Host "Service '$ServiceName' is $($svc.Status)." -ForegroundColor Green
Write-Host "  URL:        http://localhost:$Port" -ForegroundColor Green
Write-Host "  App logs:   $LogDir\pullbox.log" -ForegroundColor Green
Write-Host "  Crash logs: $stderr" -ForegroundColor Green
Write-Host ""
Write-Host "Manage with:" -ForegroundColor DarkGray
Write-Host "  $Nssm restart $ServiceName" -ForegroundColor DarkGray
Write-Host "  $Nssm edit    $ServiceName    # GUI for all the above" -ForegroundColor DarkGray
