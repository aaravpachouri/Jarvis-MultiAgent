$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runner = Join-Path $ProjectRoot 'scripts\run_daily_quant.ps1'
if (-not (Test-Path $Runner)) { throw "Daily Quant runner not found: $Runner" }
$TaskName = 'JARVIS_Daily_Quant_Market_Brief'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At '8:30AM'
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'Generate a source-linked, read-only JARVIS Quant Indian market brief at 8:30 AM local time.' -Force | Out-Null
Write-Host "Installed $TaskName. It runs daily at 8:30 AM local time while this Windows user is logged in and the PC is awake."
Write-Host "Reports: $ProjectRoot\data\reports\market_brief_latest.md"
Write-Host 'To test now, run: powershell -ExecutionPolicy Bypass -File .\scripts\run_daily_quant.ps1'
