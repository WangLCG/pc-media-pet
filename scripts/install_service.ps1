param(
    [string]$TaskName = "PC Media Pet",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runScript = Join-Path $PSScriptRoot "run.ps1"

if (-not (Test-Path $runScript)) {
    throw "Missing startup script: $runScript"
}

$actionArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`" -HostAddress $HostAddress -Port $Port"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "PC Media Pet local Tailscale/WebRTC service" -Force | Out-Null
Write-Output "Installed scheduled task '$TaskName'."
