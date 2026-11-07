# RateGuard-Lite setup for Windows. No admin rights needed.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
    foreach ($cmd in @("py -3.12", "py -3.11", "python", "python3")) {
        try {
            $parts = $cmd.Split(" ")
            $exe = $parts[0]; $args1 = @($parts | Select-Object -Skip 1)
            $v = & $exe @args1 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $maj, $min = $v.Split(".");
                if ([int]$maj -eq 3 -and [int]$min -ge 11) { return ,@($exe) + $args1 }
            }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.11 or newer was not found." -ForegroundColor Red
    Write-Host "Install it from python.org (tick 'Add python.exe to PATH', untick 'for all users'), then re-run .\setup.ps1"
    exit 1
}
$exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
Write-Host "Using Python: $(& $exe @rest --version)"

if (-not (Test-Path ".venv")) { & $exe @rest -m venv .venv }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --quiet --upgrade pip
if (Test-Path "requirements.lock") {
    & $venvPy -m pip install --quiet -r requirements.lock
}
& $venvPy -m pip install --quiet -e ".[dev]"

Write-Host "Running self-test..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot ".venv\Scripts\rgl.exe") selftest
if ($LASTEXITCODE -ne 0) { Write-Host "Self-test failed." -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host ""
Write-Host "Ready. Activate with:  .\.venv\Scripts\Activate.ps1   then try:  rgl --help" -ForegroundColor Green
