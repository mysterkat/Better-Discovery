#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up an embedded CPython 3.11 runtime for BETTER DISCOVERY.

.DESCRIPTION
    Downloads the official Python 3.11 embeddable package for Windows x64,
    installs pip into it, then installs every dependency required by the
    FastAPI backend and the MONTE CARLO toolkit.

    Run this script ONCE before building the production installer:
        Set-ExecutionPolicy -Scope Process Bypass
        .\setup_embedded_python.ps1

    After completion, build the installer with:
        npm run tauri -- build

.NOTES
    Internet access is required (downloads ~50 MB + wheels).
    The embedded runtime is written to:
        src-tauri\binaries\python\
    This directory is git-ignored (only .gitkeep is tracked).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ─── Configuration ────────────────────────────────────────────────────────────
$PY_VERSION   = "3.11.9"
$PY_ZIP_NAME  = "python-$PY_VERSION-embed-amd64.zip"
$PY_URL       = "https://www.python.org/ftp/python/$PY_VERSION/$PY_ZIP_NAME"
$GET_PIP_URL  = "https://bootstrap.pypa.io/get-pip.py"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonDir    = Join-Path $ScriptDir "src-tauri\binaries\python"
$TempZip      = Join-Path $env:TEMP $PY_ZIP_NAME
$TempGetPip   = Join-Path $env:TEMP "get-pip.py"

# ─── Helpers ──────────────────────────────────────────────────────────────────
function Write-Step ([string]$msg) {
    Write-Host "`n  → $msg" -ForegroundColor Cyan
}
function Write-Ok ([string]$msg) {
    Write-Host "    ✓ $msg" -ForegroundColor Green
}
function Write-Warn ([string]$msg) {
    Write-Host "    ⚠ $msg" -ForegroundColor Yellow
}

# ─── 0. Pre-flight ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  BETTER DISCOVERY — Embedded Python Setup" -ForegroundColor White
Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray

if (-not (Test-Path $PythonDir)) {
    New-Item -ItemType Directory -Path $PythonDir | Out-Null
}

$existingPython = Join-Path $PythonDir "python.exe"
if (Test-Path $existingPython) {
    Write-Warn "python.exe already present at: $PythonDir"
    $choice = Read-Host "  Re-install? [y/N]"
    if ($choice -notmatch '^[Yy]') {
        Write-Host "  Aborted." -ForegroundColor Yellow
        exit 0
    }
    # Remove existing installation (keep .gitkeep)
    Get-ChildItem -Path $PythonDir -Exclude ".gitkeep" | Remove-Item -Recurse -Force
}

# ─── 1. Download embeddable zip ───────────────────────────────────────────────
Write-Step "Downloading Python $PY_VERSION embeddable package…"
if (Test-Path $TempZip) {
    Write-Warn "Cached zip found at $TempZip — skipping download."
} else {
    Invoke-WebRequest -Uri $PY_URL -OutFile $TempZip -UseBasicParsing
    Write-Ok "Downloaded $PY_ZIP_NAME"
}

# ─── 2. Extract ───────────────────────────────────────────────────────────────
Write-Step "Extracting to $PythonDir …"
Expand-Archive -Path $TempZip -DestinationPath $PythonDir -Force
Write-Ok "Extracted"

# ─── 3. Enable site-packages (uncomment 'import site' in ._pth) ───────────────
Write-Step "Enabling site-packages…"
$pthFile = Get-ChildItem -Path $PythonDir -Filter "python*._pth" | Select-Object -First 1
if ($null -eq $pthFile) {
    throw "Could not find python*._pth in $PythonDir"
}
$pthContent = Get-Content $pthFile.FullName -Raw
if ($pthContent -match '#import site') {
    $pthContent = $pthContent -replace '#import site', 'import site'
    Set-Content -Path $pthFile.FullName -Value $pthContent -Encoding UTF8
    Write-Ok "Patched $($pthFile.Name)"
} else {
    Write-Ok "$($pthFile.Name) already has 'import site' enabled"
}

# ─── 4. Install pip ───────────────────────────────────────────────────────────
Write-Step "Installing pip…"
Invoke-WebRequest -Uri $GET_PIP_URL -OutFile $TempGetPip -UseBasicParsing
& $existingPython $TempGetPip --no-warn-script-location 2>&1 | Out-Null
Write-Ok "pip installed"

# ─── 5. Upgrade pip / setuptools / wheel ──────────────────────────────────────
Write-Step "Upgrading pip, setuptools, wheel…"
& $existingPython -m pip install --upgrade pip setuptools wheel --quiet
Write-Ok "Done"

# ─── 6. Install backend + MONTE CARLO requirements ────────────────────────────
Write-Step "Installing backend requirements (FastAPI, uvicorn, pydantic, …)…"
$ReqFile = Join-Path $ScriptDir "backend\requirements.txt"
& $existingPython -m pip install -r $ReqFile --quiet
Write-Ok "Backend requirements installed"

Write-Step "Installing MONTE CARLO toolkit requirements (numpy, pandas, scipy, sklearn, plotly, …)…"
$McPackages = @(
    "plotly>=5.20",
    "scipy>=1.12",
    "scikit-learn>=1.4",
    "matplotlib>=3.8",
    "beautifulsoup4>=4.12",
    "lxml>=5.0"
)
foreach ($pkg in $McPackages) {
    & $existingPython -m pip install $pkg --quiet
    Write-Ok $pkg
}

# ─── 7. Verify key imports ────────────────────────────────────────────────────
Write-Step "Verifying key imports…"
$checks = @("fastapi", "uvicorn", "numpy", "pandas", "plotly", "scipy", "sklearn", "bs4")
foreach ($mod in $checks) {
    $result = & $existingPython -c "import $mod; print('ok')" 2>&1
    if ($result -eq 'ok') {
        Write-Ok $mod
    } else {
        Write-Warn "${mod}: $result"
    }
}

# ─── 8. Summary ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Embedded Python ready at:" -ForegroundColor White
Write-Host "    $PythonDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next step — build the production installer:" -ForegroundColor White
Write-Host "    npm run tauri -- build" -ForegroundColor Yellow
Write-Host ""
