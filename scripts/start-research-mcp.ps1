$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo "src-tauri\binaries\python\python.exe"
$server = Join-Path $repo "backend\tools\research_mcp.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Bundled Python not found: $python"
}

& $python $server
exit $LASTEXITCODE

