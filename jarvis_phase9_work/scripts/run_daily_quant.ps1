$ErrorActionPreference = 'Stop'
# Python report text includes Unicode (for example ₹). Keep Python's encoding and
# Windows PowerShell's native-process decoder aligned so redirected output is safe.
$PreviousPythonIOEncoding = $env:PYTHONIOENCODING
$PreviousConsoleOutputEncoding = [Console]::OutputEncoding
$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = $Utf8Encoding
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
$ReportsDir = Join-Path $ProjectRoot 'data\reports'
New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null
$Stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$ReportPath = Join-Path $ReportsDir "market_brief_$Stamp.md"
$LatestPath = Join-Path $ReportsDir 'market_brief_latest.md'
Push-Location $ProjectRoot
try {
    $Output = & $Python 'main.py' 'run' 'Daily Indian stock market brief' 2>&1 | Out-String
    $ExitCode = $LASTEXITCODE
    if ([string]::IsNullOrWhiteSpace($Output)) { $Output = 'JARVIS Quant returned no output.' }
    $Header = "# JARVIS Quant Daily Market Brief`r`n`r`nGenerated locally: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')`r`n`r`n"
    $Body = $Header + $Output.Trim() + "`r`n"
    Set-Content -LiteralPath $ReportPath -Value $Body -Encoding UTF8
    Set-Content -LiteralPath $LatestPath -Value $Body -Encoding UTF8
    if ($ExitCode -ne 0) { throw "JARVIS market brief failed with exit code $ExitCode. See $ReportPath" }
    Write-Output "JARVIS market brief saved to: $ReportPath"
} finally {
    Pop-Location
    if ($null -eq $PreviousPythonIOEncoding) {
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $PreviousPythonIOEncoding
    }
    [Console]::OutputEncoding = $PreviousConsoleOutputEncoding
}
