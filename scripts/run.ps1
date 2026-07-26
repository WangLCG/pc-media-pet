param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Virtual environment is missing. Create it with: python -m venv .venv"
}

& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host $HostAddress --port $Port
