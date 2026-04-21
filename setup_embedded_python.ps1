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
# NOTE: do NOT use ErrorActionPreference = Stop here.
# In PS 5.1, native exe stderr lines become NativeCommandError objects
# when piped, and Stop would abort the script on any diagnostic output.
# We check $LASTEXITCODE explicitly after each native call instead.
$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
$PY_VERSION  = "3.11.9"
$PY_ZIP_NAME = "python-$PY_VERSION-embed-amd64.zip"
$PY_URL      = "https://www.python.org/ftp/python/$PY_VERSION/$PY_ZIP_NAME"
$GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
# Install Python to a persistent LocalAppData location so it survives
# reinstalls without needing to be bundled inside the NSIS installer.
$PythonDir   = Join-Path $env:LOCALAPPDATA "BETTER DISCOVERY\python"
$TempZip     = Join-Path $env:TEMP $PY_ZIP_NAME
$TempGetPip  = Join-Path $env:TEMP "get-pip.py"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "  >> $msg" -ForegroundColor Cyan
}
function Write-Ok([string]$msg) {
    Write-Host "     [OK] $msg" -ForegroundColor Green
}
function Write-Warn([string]$msg) {
    Write-Host "     [!!] $msg" -ForegroundColor Yellow
}
function Assert-Exit([string]$label) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  [FAILED] $label exited with code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 0. Pre-flight
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  BETTER DISCOVERY -- Embedded Python Setup" -ForegroundColor White
Write-Host "  ------------------------------------------" -ForegroundColor DarkGray

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
    Get-ChildItem -Path $PythonDir -Exclude ".gitkeep" |
        Remove-Item -Recurse -Force
}

# ---------------------------------------------------------------------------
# 1. Download embeddable zip
# ---------------------------------------------------------------------------
Write-Step "Downloading Python $PY_VERSION embeddable package..."
if (Test-Path $TempZip) {
    Write-Warn "Cached zip found -- skipping download."
} else {
    Invoke-WebRequest -Uri $PY_URL -OutFile $TempZip -UseBasicParsing
    Write-Ok "Downloaded $PY_ZIP_NAME"
}

# ---------------------------------------------------------------------------
# 2. Extract
# ---------------------------------------------------------------------------
Write-Step "Extracting to $PythonDir ..."
Expand-Archive -Path $TempZip -DestinationPath $PythonDir -Force
Write-Ok "Extracted"

# ---------------------------------------------------------------------------
# 3. Enable site-packages  (uncomment 'import site' in python311._pth)
# ---------------------------------------------------------------------------
Write-Step "Enabling site-packages..."
$pthFile = Get-ChildItem -Path $PythonDir -Filter "python*._pth" |
           Select-Object -First 1
if ($null -eq $pthFile) {
    Write-Host "  [FAILED] Could not find python*._pth in $PythonDir" -ForegroundColor Red
    exit 1
}
$pthContent = Get-Content $pthFile.FullName -Raw
if ($pthContent -match '#import site') {
    $pthContent = $pthContent -replace '#import site', 'import site'
    # ASCII encoding - avoids the UTF-8 BOM that PS 5.1 adds with -Encoding UTF8.
    # A BOM (\ufeff) prepended to "python311.zip" makes Python look for a file
    # literally named "\ufeffpython311.zip" and fail with ModuleNotFoundError.
    [System.IO.File]::WriteAllText(
        $pthFile.FullName,
        $pthContent,
        [System.Text.UTF8Encoding]::new($false)   # $false = no BOM
    )
    Write-Ok "Patched $($pthFile.Name)"
} else {
    Write-Ok "$($pthFile.Name) already has 'import site' enabled"
}

# ---------------------------------------------------------------------------
# 4. Install pip
#    Run python from ITS OWN directory so it resolves python311.zip correctly.
# ---------------------------------------------------------------------------
Write-Step "Installing pip..."
Invoke-WebRequest -Uri $GET_PIP_URL -OutFile $TempGetPip -UseBasicParsing

Push-Location $PythonDir
& ".\python.exe" $TempGetPip --no-warn-script-location
Pop-Location
Assert-Exit "get-pip.py"
Write-Ok "pip installed"

# ---------------------------------------------------------------------------
# 5. Upgrade pip / setuptools / wheel
# ---------------------------------------------------------------------------
Write-Step "Upgrading pip, setuptools, wheel..."
Push-Location $PythonDir
& ".\python.exe" -m pip install --upgrade pip setuptools wheel
Pop-Location
Assert-Exit "pip upgrade"
Write-Ok "Done"

# ---------------------------------------------------------------------------
# 6. Install backend requirements
# ---------------------------------------------------------------------------
Write-Step "Installing backend requirements (FastAPI, uvicorn, pydantic, ...)..."
$ReqFile = Join-Path $ScriptDir "backend\requirements.txt"
Push-Location $PythonDir
& ".\python.exe" -m pip install -r $ReqFile
Pop-Location
Assert-Exit "pip install -r requirements.txt"
Write-Ok "Backend requirements installed"

# ---------------------------------------------------------------------------
# 7. Install MONTE CARLO toolkit requirements
# ---------------------------------------------------------------------------
Write-Step "Installing MONTE CARLO requirements (scipy, sklearn, matplotlib, plotly, ...)..."
$McPackages = @(
    "plotly>=5.20",
    "scipy>=1.12",
    "scikit-learn>=1.4",
    "matplotlib>=3.8",
    "beautifulsoup4>=4.12",
    "lxml>=5.0"
)
Push-Location $PythonDir
foreach ($pkg in $McPackages) {
    & ".\python.exe" -m pip install $pkg
    Assert-Exit "pip install $pkg"
    Write-Ok $pkg
}
Pop-Location

# ---------------------------------------------------------------------------
# 8. Verify key imports
# ---------------------------------------------------------------------------
Write-Step "Verifying key imports..."
$checks = @("fastapi", "uvicorn", "numpy", "pandas", "plotly", "scipy", "sklearn", "bs4")
Push-Location $PythonDir
foreach ($mod in $checks) {
    $result = & ".\python.exe" -c "import $mod; print('ok')" 2>&1
    if ("$result".Trim() -eq 'ok') {
        Write-Ok $mod
    } else {
        Write-Warn "${mod}: $result"
    }
}
Pop-Location

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Embedded Python ready at:" -ForegroundColor White
Write-Host "    $PythonDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next step -- build the production installer:" -ForegroundColor White
Write-Host "    npm run tauri -- build" -ForegroundColor Yellow
Write-Host ""
