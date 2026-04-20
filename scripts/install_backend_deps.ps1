# Install backend deps into the dev .venv. Used once during setup;
# the embedded Python runtime (Phase 8) is installed via
# setup_embedded_python.ps1.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv/Scripts/python.exe")) {
    Write-Host "Creating .venv..." -ForegroundColor Cyan
    python -m venv .venv
}

& ".venv/Scripts/python.exe" -m pip install --disable-pip-version-check `
    --upgrade pip
& ".venv/Scripts/python.exe" -m pip install --disable-pip-version-check `
    -r backend/requirements.txt

Write-Host "Done." -ForegroundColor Green
