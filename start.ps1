# Starts LiveSim on Windows. First run creates .venv and installs dependencies.
# Extra arguments are passed through, e.g.:  .\start.ps1 --port 8080 --history-days 7
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating a virtual environment in .venv ..."
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv } else { python -m venv .venv }
    & $python -m pip install --quiet --disable-pip-version-check --upgrade pip
}
Write-Host "Checking dependencies ..."
& $python -m pip install --quiet --disable-pip-version-check -r requirements.txt
& $python -m livesim @args
