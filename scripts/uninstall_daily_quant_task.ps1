$ErrorActionPreference = 'Stop'
$TaskName = 'JARVIS_Daily_Quant_Market_Brief'
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName"
} else {
    Write-Host "Scheduled task not found: $TaskName"
}
