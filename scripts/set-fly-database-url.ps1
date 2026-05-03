<#
.SYNOPSIS
  Reads DATABASE_URL from repo .env, normalizes it, sets Fly secret (no broken quoting).

.PARAMETER App
  Fly app name (default: from fly.toml).

.PARAMETER Deploy
  If set, secrets import also triggers a rolling deploy (needs stable DNS to api.fly.io / api.machines.dev).
  Default is -StageOnly: update DATABASE_URL only; run deploy yourself when the network is OK.

.EXAMPLE
  .\scripts\set-fly-database-url.ps1
  flyctl deploy -a bar8-stocky --remote-only --wait-timeout 10m

.EXAMPLE
  .\scripts\set-fly-database-url.ps1 -Deploy
#>
param(
    [string]$App = "",
    [switch]$Deploy
)

$ErrorActionPreference = "Stop"

function Read-TextFileUtf8StripBom([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $bytes = $bytes[3..($bytes.Length - 1)]
    }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Error ".env not found at $envPath"
}

if (-not $App) {
    $flyToml = Join-Path $repoRoot "fly.toml"
    $tomlText = Read-TextFileUtf8StripBom $flyToml
    foreach ($line in ($tomlText -split "`r?`n")) {
        if ($line -match '^\s*app\s*=\s*"([^"]+)"') {
            $App = $Matches[1]
            break
        }
    }
}
if (-not $App) {
    Write-Error "Could not determine app name (fly.toml or -App)."
}

$envText = Read-TextFileUtf8StripBom $envPath
$rawLine = @(
    $envText -split "`r?`n" |
        ForEach-Object { $_.TrimStart([char]0xFEFF) } |
        Where-Object { $_ -match '^\s*DATABASE_URL\s*=' }
)
if ($rawLine.Count -eq 0) {
    Write-Error "No DATABASE_URL= line in .env"
}
$val = ($rawLine[0] -replace '^\s*DATABASE_URL\s*=\s*', "").Trim()
if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
    $val = $val.Substring(1, $val.Length - 2).Trim()
}
$val = $val.Replace("`r", "").Replace("`n", "")
if (-not $val.StartsWith("postgresql://") -and -not $val.StartsWith("postgres://")) {
    Write-Error "DATABASE_URL must start with postgresql:// or postgres://"
}

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
    [System.Environment]::GetEnvironmentVariable("Path", "User")
$fly = Get-Command flyctl -ErrorAction SilentlyContinue
if (-not $fly) {
    $fly = Get-Command fly -ErrorAction SilentlyContinue
}
if (-not $fly) {
    Write-Error "Fly CLI not found (flyctl)."
}

Write-Host "Setting DATABASE_URL on Fly app '$App' (length $($val.Length); value not printed)..."
if (-not $Deploy) {
    Write-Host "(Using --stage: secret updates only; no rolling deploy in this step - avoids flaky DNS mid-roll.)"
}
# Temp file UTF-8 without BOM: piping a string can prepend BOM and break the secret name (\ufeffDATABASE_URL).
$tmp = [System.IO.Path]::GetTempFileName()
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$importArgs = @("secrets", "import", "-a", $App)
if (-not $Deploy) {
    $importArgs += "--stage"
}
try {
    [System.IO.File]::WriteAllText($tmp, "DATABASE_URL=$val", $utf8NoBom)
    $p = Start-Process -FilePath $fly.Source -ArgumentList $importArgs `
        -RedirectStandardInput $tmp -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -ne 0) {
        Write-Error "fly secrets import exited with code $($p.ExitCode). If you saw 'no such host' for api.machines.dev, fix DNS/VPN and retry, or use default --stage then deploy separately."
    }
}
finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}

if (-not $Deploy) {
    Write-Host "Secret staged. Machines still run the old env until you deploy. Next:"
}
Write-Host "  flyctl deploy -a $App --remote-only --wait-timeout 10m"
Write-Host "If the machine was stuck (max restarts), deploy recreates the release."
