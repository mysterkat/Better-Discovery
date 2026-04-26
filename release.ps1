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

$pkgFile = Join-Path $ScriptDir "package.json"
$cfgFile = Join-Path $ScriptDir "src-tauri\tauri.conf.json"

$pkg = [System.IO.File]::ReadAllText($pkgFile)
$pkg = $pkg -replace '"version":\s*"[^"]+"', "`"version`": `"$Version`""
[System.IO.File]::WriteAllText($pkgFile, $pkg, [System.Text.UTF8Encoding]::new($false))

$cfg = [System.IO.File]::ReadAllText($cfgFile)
$cfg = $cfg -replace '"version":\s*"[^"]+"', "`"version`": `"$Version`""
[System.IO.File]::WriteAllText($cfgFile, $cfg, [System.Text.UTF8Encoding]::new($false))

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
$bundleDir  = Join-Path $ScriptDir "src-tauri\target\release\bundle"
$nsisDir    = Join-Path $bundleDir "nsis"
$latestJson = Join-Path $bundleDir "latest.json"

$installer = Get-ChildItem $nsisDir -Filter "*setup.exe" | Where-Object { $_.Name -notlike "*.zip" } | Select-Object -First 1
$zipFile   = Get-ChildItem $nsisDir -Filter "*.nsis.zip"     | Select-Object -First 1
$sigFile   = Get-ChildItem $nsisDir -Filter "*.nsis.zip.sig" | Select-Object -First 1

if (-not $installer) {
    Write-Host "  [ERROR] Installer not found in $nsisDir" -ForegroundColor Red
    exit 1
}

# ── 5. Create .nsis.zip if Tauri didn't ──────────────────────────────────────
if (-not $zipFile) {
    Write-Host ""
    Write-Host "  >> Creating updater zip..." -ForegroundColor Cyan
    $zipPath = Join-Path $nsisDir ($installer.BaseName + ".nsis.zip")
    Compress-Archive -Path $installer.FullName -DestinationPath $zipPath -Force
    $zipFile = Get-Item $zipPath
    Write-Host "     [OK] $($zipFile.Name)" -ForegroundColor Green
}

# ── 6. Sign .nsis.zip if not already signed ───────────────────────────────────
if (-not $sigFile) {
    Write-Host ""
    Write-Host "  >> Signing updater zip..." -ForegroundColor Cyan
    $keyContent = [System.IO.File]::ReadAllText($KeyFile).Trim()
    Push-Location $ScriptDir
    & npm run tauri -- signer sign -k $keyContent $zipFile.FullName
    $signExit = $LASTEXITCODE
    Pop-Location
    if ($signExit -ne 0) {
        Write-Host "  [FAILED] tauri signer sign exited $signExit" -ForegroundColor Red
        exit 1
    }
    $sigFile = Get-Item ($zipFile.FullName + ".sig") -ErrorAction SilentlyContinue
    if (-not $sigFile) {
        Write-Host "  [ERROR] .sig file not created after signing" -ForegroundColor Red
        exit 1
    }
    Write-Host "     [OK] $($sigFile.Name)" -ForegroundColor Green
}

# ── 7. Generate latest.json ───────────────────────────────────────────────────
if (-not (Test-Path $latestJson)) {
    Write-Host ""
    Write-Host "  >> Generating latest.json..." -ForegroundColor Cyan

    $sig     = [System.IO.File]::ReadAllText($sigFile.FullName).Trim()
    $zipUrl  = "https://github.com/mysterkat/Better-Discovery-releases/releases/download/v$Version/$($zipFile.Name)"
    $pubDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    $json = [ordered]@{
        version   = $Version
        notes     = $Notes
        pub_date  = $pubDate
        platforms = [ordered]@{
            "windows-x86_64" = [ordered]@{
                signature = $sig
                url       = $zipUrl
            }
        }
    }

    $jsonStr = $json | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($latestJson, $jsonStr, [System.Text.UTF8Encoding]::new($false))
    Write-Host "     [OK] latest.json generated" -ForegroundColor Green
}

Write-Host ""
Write-Host "  >> Artifacts ready:" -ForegroundColor Cyan
Write-Host "     $($installer.Name)" -ForegroundColor Gray
Write-Host "     $($zipFile.Name)" -ForegroundColor Gray
Write-Host "     $($sigFile.Name)" -ForegroundColor Gray
Write-Host "     latest.json" -ForegroundColor Gray

# ── 9. Commit version bump ────────────────────────────────────────────────────
Write-Host ""
Write-Host "  >> Committing version bump..." -ForegroundColor Cyan
Push-Location $ScriptDir
git add package.json src-tauri/tauri.conf.json
git commit -m "chore: bump version to $Version"
git push
$pushExit = $LASTEXITCODE
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
if ($zipFile) { $uploadFiles += $zipFile.FullName }
if ($sigFile) { $uploadFiles += $sigFile.FullName }

& gh release create "v$Version" `
    --repo "mysterkat/Better-Discovery-releases" `
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
Write-Host "  https://github.com/mysterkat/Better-Discovery-releases/releases/tag/v$Version" -ForegroundColor Cyan
Write-Host ""
