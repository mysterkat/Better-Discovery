# Phase 4 dev helper. `npm run tauri dev` already runs Vite via
# beforeDevCommand and spawns the Python sidecar from Rust, so this script is
# mostly a convenience wrapper.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv/Scripts/python.exe") -and -not (Test-Path "src-tauri/binaries/python/python.exe")) {
    Write-Host "No Python interpreter found; creating a dev .venv..." -ForegroundColor Yellow
    python -m venv .venv
    & ".venv/Scripts/python.exe" -m pip install --disable-pip-version-check -q `
        fastapi "uvicorn[standard]" pydantic numpy pandas plotly httpx
}

npm run tauri -- dev
