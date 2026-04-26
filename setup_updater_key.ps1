#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$KeyDir     = Join-Path $env:USERPROFILE ".tauri"
$KeyFile    = Join-Path $KeyDir "betterdiscovery.key"
$ConfigFile = Join-Path $ScriptDir "src-tauri\tauri.conf.json"

Write-Host ""
Write-Host "  BETTER DISCOVERY -- Updater Key Setup" -ForegroundColor White
Write-Host "  --------------------------------------" -ForegroundColor DarkGray

if (Test-Path $KeyFile) {
    Write-Host ""
    Write-Host "  Key already exists at: $KeyFile" -ForegroundColor Yellow
    Write-Host "  Delete it first if you want to regenerate." -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

if (-not (Test-Path $KeyDir)) {
    New-Item -ItemType Directory -Path $KeyDir | Out-Null
}

Write-Host ""
Write-Host "  >> Generating Ed25519 keypair..." -ForegroundColor Cyan

$output = & npm run tauri -- signer generate -w $KeyFile 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAILED] tauri signer generate exited $LASTEXITCODE" -ForegroundColor Red
    $output | ForEach-Object { Write-Host "    $_" }
    exit 1
}

$pubkeyLine = $null
$foundLabel = $false
foreach ($line in $output) {
    $trimmed = $line.ToString().Trim()
    if ($foundLabel -and $trimmed.Length -gt 30) {
        $pubkeyLine = $trimmed
        break
    }
    if ($trimmed -match 'public key') { $foundLabel = $true }
    if ($trimmed -match '^[A-Za-z0-9+/=]{40,}$') { $pubkeyLine = $trimmed }
}

if (-not $pubkeyLine) {
    Write-Host ""
    Write-Host "  Could not extract public key. Full output:" -ForegroundColor Red
    $output | ForEach-Object { Write-Host "    $_" }
    Write-Host ""
    Write-Host "  Manually copy the public key into src-tauri\tauri.conf.json" -ForegroundColor Yellow
    exit 1
}

Write-Host "     [OK] Keypair generated" -ForegroundColor Green
Write-Host "     Public key: $pubkeyLine" -ForegroundColor Gray

Write-Host ""
Write-Host "  >> Patching tauri.conf.json..." -ForegroundColor Cyan

$config = Get-Content $ConfigFile -Raw
$placeholder = 'REPLACE_WITH_PUBLIC_KEY_RUN_setup_updater_key.ps1'

if ($config -notmatch [regex]::Escape($placeholder)) {
    Write-Host "     tauri.conf.json already has a pubkey -- not overwriting." -ForegroundColor Yellow
} else {
    $config = $config.Replace($placeholder, $pubkeyLine)
    [System.IO.File]::WriteAllText($ConfigFile, $config, [System.Text.UTF8Encoding]::new($false))
    Write-Host "     [OK] pubkey written to tauri.conf.json" -ForegroundColor Green
}

Write-Host ""
Write-Host "  --------------------------------------" -ForegroundColor DarkGray
Write-Host "  Private key saved to:" -ForegroundColor White
Write-Host "    $KeyFile" -ForegroundColor Cyan
Write-Host ""
Write-Host "  IMPORTANT: back up that file somewhere safe." -ForegroundColor Yellow
Write-Host "  If you lose it you cannot sign future updates." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Next step -- create a release:" -ForegroundColor White
$example = '    .\release.ps1 -Version "0.1.0" -Notes "Initial release"'
Write-Host $example -ForegroundColor Yellow
Write-Host ""
