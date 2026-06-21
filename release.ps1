#Requires -Version 5.1
param(
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$Notes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$KeyFile   = Join-Path $env:USERPROFILE ".tauri\betterdiscovery.key"

# ── Guards ────────────────────────────────────────────────────────────────────
if (-not (Test-Path $KeyFile)) {
    Write-Host "  [ERROR] Key not found. Run setup_updater_key.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "  [ERROR] GitHub CLI not found: https://cli.github.com" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  BETTER DISCOVERY -- Release v$Version" -ForegroundColor White
Write-Host "  ------------------------------------" -ForegroundColor DarkGray

# ── 1. Bump version ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Bumping version to $Version..." -ForegroundColor Cyan

$cfgFile = Join-Path $ScriptDir "src-tauri\tauri.conf.json"
$cargoFile = Join-Path $ScriptDir "src-tauri\Cargo.toml"
$cargoLockFile = Join-Path $ScriptDir "src-tauri\Cargo.lock"

Push-Location $ScriptDir
npm version $Version --no-git-tag-version --allow-same-version | Out-Null
$npmVersionExit = $LASTEXITCODE
Pop-Location
if ($npmVersionExit -ne 0) {
    Write-Host "  [FAILED] npm version exited $npmVersionExit" -ForegroundColor Red
    exit 1
}

$cfg = [System.IO.File]::ReadAllText($cfgFile)
$cfg = $cfg -replace '"version":\s*"[^"]+"', "`"version`": `"$Version`""
[System.IO.File]::WriteAllText($cfgFile, $cfg, [System.Text.UTF8Encoding]::new($false))

$cargo = [System.IO.File]::ReadAllText($cargoFile)
$cargo = $cargo -replace '(?m)(^version\s*=\s*)"[^"]+"', "`$1`"$Version`""
[System.IO.File]::WriteAllText($cargoFile, $cargo, [System.Text.UTF8Encoding]::new($false))

$cargoLock = [System.IO.File]::ReadAllText($cargoLockFile)
$cargoLock = $cargoLock -replace '(?ms)(name = "better-discovery"\r?\nversion = )"[^"]+"', "`$1`"$Version`""
[System.IO.File]::WriteAllText($cargoLockFile, $cargoLock, [System.Text.UTF8Encoding]::new($false))

Write-Host "     [OK] Version updated" -ForegroundColor Green

# ── 2. Load signing key ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Loading signing key..." -ForegroundColor Cyan
$env:TAURI_SIGNING_PRIVATE_KEY = [System.IO.File]::ReadAllText($KeyFile).Trim()
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
Write-Host "     [OK] Key loaded" -ForegroundColor Green

# ── 3. Build ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Building (this takes a few minutes)..." -ForegroundColor Cyan
Push-Location $ScriptDir
npm run tauri -- build
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) {
    Write-Host "  [FAILED] Build exited $buildExit" -ForegroundColor Red
    exit 1
}
Write-Host "     [OK] Build complete" -ForegroundColor Green

# ── 4. Locate artifacts ───────────────────────────────────────────────────────
# Tauri v2 (createUpdaterArtifacts=true) produces the NSIS installer
# (-setup.exe) and its updater signature (-setup.exe.sig) during the build
# above, since TAURI_SIGNING_PRIVATE_KEY was set. The updater downloads and
# runs the .exe directly — there is NO .nsis.zip in v2.
$bundleDir  = Join-Path $ScriptDir "src-tauri\target\release\bundle"
$nsisDir    = Join-Path $bundleDir "nsis"
$latestJson = Join-Path $bundleDir "latest.json"

$installer = Get-ChildItem $nsisDir -Filter "*_${Version}_x64-setup.exe"     | Select-Object -First 1
$sigFile   = Get-ChildItem $nsisDir -Filter "*_${Version}_x64-setup.exe.sig" | Select-Object -First 1

if (-not $installer) {
    Write-Host "  [ERROR] Installer (-setup.exe) not found in $nsisDir" -ForegroundColor Red
    exit 1
}
if (-not $sigFile) {
    Write-Host "  [ERROR] Updater signature (-setup.exe.sig) not found — was the build signed?" -ForegroundColor Red
    exit 1
}

# ── 5. Generate latest.json ───────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Generating latest.json..." -ForegroundColor Cyan

$sig     = [System.IO.File]::ReadAllText($sigFile.FullName).Trim()
$assetName = [Uri]::EscapeDataString($installer.Name)
$exeUrl  = "https://github.com/mysterkat/better-discovery-releases/releases/download/v$Version/$assetName"
$pubDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# version MUST be bare semver (no "v") — Tauri's updater parses it with the
# semver crate, which rejects a leading "v" and fails the whole update check.
$json = [ordered]@{
    version   = $Version
    notes     = $Notes
    pub_date  = $pubDate
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $sig
            url       = $exeUrl
        }
    }
}

$jsonStr = $json | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($latestJson, $jsonStr, [System.Text.UTF8Encoding]::new($false))
Write-Host "     [OK] latest.json generated" -ForegroundColor Green

Write-Host ""
Write-Host "  >> Artifacts ready:" -ForegroundColor Cyan
Write-Host "     $($installer.Name)" -ForegroundColor Gray
Write-Host "     $($sigFile.Name)" -ForegroundColor Gray
Write-Host "     latest.json" -ForegroundColor Gray

# ── 9. Commit version bump ────────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Committing version bump..." -ForegroundColor Cyan
Push-Location $ScriptDir
git add package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock
git commit -m "chore: bump version to $Version"
git push
$pushExit = $LASTEXITCODE
if ($pushExit -eq 0) {
    git tag -a "v$Version" -m "BETTER DISCOVERY v$Version"
    git push origin "v$Version"
    $pushExit = $LASTEXITCODE
}
Pop-Location
if ($pushExit -ne 0) {
    Write-Host "  [FAILED] git push exited $pushExit" -ForegroundColor Red
    exit 1
}
Write-Host "     [OK] Pushed" -ForegroundColor Green

# ── 10. Create GitHub release ─────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Creating GitHub release v$Version..." -ForegroundColor Cyan

$uploadFiles = @($installer.FullName, $latestJson)
if ($sigFile) { $uploadFiles += $sigFile.FullName }

& gh release create "v$Version" `
    --repo "mysterkat/better-discovery-releases" `
    --title "v$Version" `
    --notes $Notes `
    @uploadFiles

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAILED] gh release create failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  ------------------------------------" -ForegroundColor DarkGray
Write-Host "  Release v$Version published!" -ForegroundColor Green
Write-Host "  https://github.com/mysterkat/better-discovery-releases/releases/tag/v$Version" -ForegroundColor Cyan
Write-Host ""
