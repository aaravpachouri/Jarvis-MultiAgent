$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if (Test-Path '.venv\Scripts\python.exe') {
    & '.venv\Scripts\python.exe' 'dashboard_server.py'
} else {
    python 'dashboard_server.py'
}
