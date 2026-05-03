<#
.SYNOPSIS
  First-time Fly.io setup for Stocky: app, volume, secrets from .env, deploy.

.DESCRIPTION
  Parses app name, region, and volume from fly.toml. Requires Fly CLI (flyctl),
  billing enabled on your Fly account, and Docker for remote builds.

.PARAMETER SkipSecretsImport
  Do not run `fly secrets import` from .env (use if secrets already set).

.PARAMETER SkipDeploy
  Only create app/volume and optionally import secrets.

.EXAMPLE
  .\scripts\fly-bootstrap.ps1
#>
param(
    [switch]$SkipSecretsImport,
    [switch]$SkipDeploy
)

# flyctl writes benign warnings to stderr; Stop would treat them as terminating errors.
$ErrorActionPreference = 'Continue'

function Get-FlyTomlValue {
    param([string]$Path, [string]$Key)
    $line = Get-Content $Path | Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } | Select-Object -First 1
    if (-not $line) { return $null }
    if ($line -match '=\s*"([^"]+)"') { return $Matches[1] }
    if ($line -match '=\s*(\S+)') { return $Matches[1].TrimEnd() }
    return $null
}

$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $repoRoot 'fly.toml'))) {
    Write-Error "fly.toml not found at repo root (expected next to scripts/). Current: $repoRoot"
    exit 1
}
Set-Location $repoRoot

$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
    [System.Environment]::GetEnvironmentVariable('Path', 'User')

$flyExe = $null
$fly = Get-Command flyctl -ErrorAction SilentlyContinue
if (-not $fly) { $fly = Get-Command fly -ErrorAction SilentlyContinue }
if ($fly) {
    $flyExe = $fly.Source
}
if (-not $flyExe) {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.fly\bin\flyctl.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\fly.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\flyctl.exe')
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) {
            $flyExe = $p
            break
        }
    }
}
if (-not $flyExe) {
    Write-Host 'Fly CLI not found in PATH.'
    Write-Host 'Install: winget install Fly-io.flyctl -e'
    Write-Host 'Then close this terminal, open a new one, or run:'
    Write-Host ("  & `"$env:USERPROFILE\.fly\bin\flyctl.exe`" version")
    Write-Host 'Login uses the same binary:'
    Write-Host ("  & `"$env:USERPROFILE\.fly\bin\flyctl.exe`" auth login")
    exit 1
}
Write-Host "Using: $flyExe"

$flyToml = Join-Path $repoRoot 'fly.toml'
$app = Get-FlyTomlValue -Path $flyToml -Key 'app'
$region = Get-FlyTomlValue -Path $flyToml -Key 'primary_region'
$mountSource = $null
foreach ($line in Get-Content $flyToml) {
    if ($line -match '^\s*source\s*=\s*"([^"]+)"') { $mountSource = $Matches[1]; break }
}
if (-not $app -or -not $region -or -not $mountSource) {
    Write-Error "Could not parse app, primary_region, or mounts.source from fly.toml"
    exit 1
}

Write-Host "App: $app   Region: $region   Volume: $mountSource"

$appsRaw = & $flyExe apps list -j 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$appsRaw)) { $appsRaw = '[]' }
try { $apps = $appsRaw | ConvertFrom-Json } catch { $apps = @() }
if (-not $apps) { $apps = @() }
$exists = $apps | Where-Object { $_.name -eq $app -or $_.Name -eq $app }
if (-not $exists) {
    Write-Host "Creating app '$app'..."
    $outLog = Join-Path $env:TEMP "fly-apps-create-$PID-out.txt"
    $errLog = Join-Path $env:TEMP "fly-apps-create-$PID-err.txt"
    try {
        $p = Start-Process -FilePath $flyExe -ArgumentList @('apps', 'create', $app) `
            -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $stdOut = if (Test-Path $outLog) { Get-Content $outLog -Raw -ErrorAction SilentlyContinue } else { '' }
        $stdErr = if (Test-Path $errLog) { Get-Content $errLog -Raw -ErrorAction SilentlyContinue } else { '' }
        if ($stdOut) { Write-Host $stdOut.TrimEnd() }
        if ($stdErr) { Write-Host $stdErr.TrimEnd() }
        $combined = "$stdOut $stdErr"
        if ($p.ExitCode -ne 0) {
            Write-Host ""
            if ($combined -match 'already been taken|Name has already been taken') {
                Write-Host "This Fly app name is already used by someone else (names are global)."
                Write-Host "Edit fly.toml: change app = `"$app`" to a unique name (e.g. bar8-stocky-catalyst), then run this script again."
            } elseif ($combined -match 'payment information|billing|credit card') {
                Write-Host "Billing may be required:"
                Write-Host "  https://fly.io/dashboard/billing"
            } else {
                Write-Host "apps create failed (exit $($p.ExitCode)). Check the output above."
                Write-Host "If billing: https://fly.io/dashboard/billing"
            }
            exit 1
        }
    } finally {
        Remove-Item $outLog, $errLog -ErrorAction SilentlyContinue
    }
}

Write-Host "Checking volumes..."
$volJson = & $flyExe volumes list -a $app -j 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$volJson)) { $volJson = '[]' }
try { $vols = $volJson | ConvertFrom-Json } catch { $vols = @() }
if (-not $vols) { $vols = @() }
$volOk = $vols | Where-Object {
    ($_.name -eq $mountSource -or $_.Name -eq $mountSource) -and
    ($_.region -eq $region -or $_.Region -eq $region)
}
if (-not $volOk) {
    Write-Host "Creating volume '$mountSource' in $region ..."
    & $flyExe volumes create $mountSource -r $region -s 1 -a $app -y
}

$envFile = Join-Path $repoRoot '.env'
if (-not $SkipSecretsImport -and (Test-Path $envFile)) {
    Write-Host "Importing secrets from .env (values not printed)..."
    $lines = @(Get-Content $envFile | Where-Object {
        $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' -and $_ -notmatch '^\s*#'
    })
    if ($lines.Length -gt 0) {
        # --stage: set secrets without an extra full deploy; we deploy once below.
        $lines | & $flyExe secrets import -a $app --stage
    }
} elseif (-not $SkipSecretsImport) {
    Write-Host "No .env found - set secrets manually, e.g. fly secrets set OPENAI_API_KEY=... LLM_MODEL=... -a $app"
}

if (-not $SkipDeploy) {
    Write-Host "Deploying..."
    & $flyExe deploy -a $app --remote-only --wait-timeout 10m
    Write-Host ""
    Write-Host "Done. Open: https://$app.fly.dev"
    Write-Host "Logs: fly logs -a $app"
}
