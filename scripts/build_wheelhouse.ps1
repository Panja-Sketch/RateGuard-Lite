<#
.SYNOPSIS
  Build an offline "wheelhouse" (a folder of Python packages) for RateGuard-Lite.

.DESCRIPTION
  Run this on a machine WITH internet access that has the SAME Python minor version as the target
  (organisation) laptop. It downloads every package in requirements.lock (plus the build tools needed
  for `pip install -e .`) as pre-built wheels for 64-bit Windows. Copy the repo folder together with the
  resulting wheelhouse\ folder to the target laptop (USB stick, file share, ...), then follow
  docs\INSTALL.md section "Offline install".

  Nothing from your own data is involved: only public packages are downloaded.

.PARAMETER PythonVersion
  Target Python as MAJOR.MINOR (default: the Python running this script, e.g. 3.12).
.PARAMETER Platform
  pip platform tag of the target machine (default win_amd64).
.PARAMETER OutDir
  Folder to fill (default: .\wheelhouse next to this repo's requirements.lock).
.PARAMETER IndexUrl
  Optional internal mirror, e.g. https://pypi.example.corp/simple (only if IT gives you one).

.EXAMPLE
  .\scripts\build_wheelhouse.ps1
  .\scripts\build_wheelhouse.ps1 -PythonVersion 3.11
#>
param(
    [string]$PythonVersion = "",
    [string]$Platform = "win_amd64",
    [string]$OutDir = "",
    [string]$IndexUrl = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -Path $repo

function Find-Python {
    foreach ($cmd in @("py -3.12", "py -3.11", "python", "python3")) {
        try {
            $parts = $cmd.Split(" ")
            $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
            $v = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) { return ,@($exe) + $rest }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) { Write-Host "Python was not found. Install Python 3.11 or 3.12 (64-bit) first." -ForegroundColor Red; exit 1 }
$exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
$have = (& $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if (-not $PythonVersion) { $PythonVersion = $have }
if ($PythonVersion -ne $have) {
    Write-Host "Note: this machine runs Python $have but you asked for $PythonVersion. That is fine for downloading;" -ForegroundColor Yellow
    Write-Host "      the wheels will match $PythonVersion, so the target laptop must run Python $PythonVersion." -ForegroundColor Yellow
}
if (-not (Test-Path "requirements.lock")) { Write-Host "requirements.lock not found in $repo" -ForegroundColor Red; exit 1 }
if (-not $OutDir) { $OutDir = Join-Path $repo "wheelhouse" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Python on this machine : $have"
Write-Host "Target                 : Python $PythonVersion, $Platform"
Write-Host "Wheelhouse folder      : $OutDir"

$common = @("--dest", $OutDir, "--only-binary=:all:", "--platform", $Platform, "--python-version", $PythonVersion, "--implementation", "cp", "--disable-pip-version-check")
if ($IndexUrl) { $common += @("--index-url", $IndexUrl) }

# 1) every pinned runtime package
& $exe @rest -m pip download -r requirements.lock @common
if ($LASTEXITCODE -ne 0) { Write-Host "Download of requirements.lock failed (see the message above)." -ForegroundColor Red; exit $LASTEXITCODE }

# 2) build tools so that 'pip install --no-index -e .' works offline
& $exe @rest -m pip download "setuptools>=68" wheel pip @common
if ($LASTEXITCODE -ne 0) { Write-Host "Download of the build tools failed (see the message above)." -ForegroundColor Red; exit $LASTEXITCODE }

$n = (Get-ChildItem -Path $OutDir -Filter *.whl).Count
$mb = [math]::Round(((Get-ChildItem -Path $OutDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "Done: $n wheel files, $mb MB in $OutDir" -ForegroundColor Green
Write-Host "Copy this whole repo folder (including wheelhouse\) to the target laptop, then see docs\INSTALL.md -> Offline install."
