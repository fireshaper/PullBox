# build-deploy.ps1
# Build the frontend, stage it into the backend, package a clean copy of the
# backend into "_TO COPY", and zip it for transfer to another machine.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File C:\Users\Rory\PullBox\build-deploy.ps1

$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$Root      = 'C:\Users\Rory\PullBox'
$Frontend  = Join-Path $Root 'frontend'
$Backend   = Join-Path $Root 'backend'
$DistDir   = Join-Path $Frontend 'dist'
$StaticDir = Join-Path $Backend 'pullbox\static'
$CopyDir   = Join-Path $Root '_TO COPY'
$ZipPath   = Join-Path $Root 'pullbox-deploy.zip'

# ── 1. Clean the _TO COPY staging folder ──────────────────────────────────────
Write-Host '==> Cleaning "_TO COPY" ...' -ForegroundColor Cyan
if (Test-Path -LiteralPath $CopyDir) {
    Remove-Item -LiteralPath $CopyDir -Recurse -Force
}
New-Item -ItemType Directory -Path $CopyDir | Out-Null

# ── 2. Build the frontend ─────────────────────────────────────────────────────
Write-Host '==> Building frontend (npm run build) ...' -ForegroundColor Cyan
Push-Location $Frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit code $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

# ── 3. Stage the build into backend\pullbox\static ────────────────────────────
Write-Host '==> Staging frontend build into backend\pullbox\static ...' -ForegroundColor Cyan
if (Test-Path -LiteralPath $StaticDir) {
    Remove-Item -LiteralPath $StaticDir -Recurse -Force
}
Copy-Item -LiteralPath $DistDir -Destination $StaticDir -Recurse

# ── 4. Copy the backend into _TO COPY, excluding junk (copy + clean in one) ────
# robocopy skips these during the copy so we never haul the multi-GB .venv around.
Write-Host '==> Copying backend into "_TO COPY" (excluding venv / caches / dbs) ...' -ForegroundColor Cyan
$BackendCopy = Join-Path $CopyDir 'backend'
$excludeDirs  = @('.venv', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', 'node_modules')
$excludeFiles = @('*.pyc', '*.pyo', '*.db', '*.db-shm', '*.db-wal')
robocopy $Backend $BackendCopy /E /XD $excludeDirs /XF $excludeFiles /NFL /NDL /NP | Out-Null
# robocopy exit codes 0-7 are success (bit flags); 8+ means a real failure.
if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit code $LASTEXITCODE)" }
$global:LASTEXITCODE = 0  # reset so a non-zero robocopy "success" code doesn't leak

# ── 5. Zip the staged folder ──────────────────────────────────────────────────
# Use .NET ZipFile (faster and more reliable than Compress-Archive) with a retry:
# freshly-copied files can be briefly locked by antivirus, the editor's file
# watcher, or a running "uvicorn --reload" dev server, which would otherwise
# abort the whole build with a "being used by another process" error.
Write-Host '==> Zipping ...' -ForegroundColor Cyan
Add-Type -AssemblyName System.IO.Compression.FileSystem

$maxAttempts = 4
for ($attempt = 1; ; $attempt++) {
    try {
        if (Test-Path -LiteralPath $ZipPath) {
            Remove-Item -LiteralPath $ZipPath -Force
        }
        [System.IO.Compression.ZipFile]::CreateFromDirectory($CopyDir, $ZipPath)
        break
    }
    catch {
        if ($attempt -ge $maxAttempts) {
            throw "Zip failed after $maxAttempts attempts: $($_.Exception.Message)"
        }
        Write-Host "   attempt $attempt failed (file locked?); retrying in 3s ..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
}

$zipMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Done. Package ready ($zipMB MB):" -ForegroundColor Green
Write-Host "  $ZipPath" -ForegroundColor Green
