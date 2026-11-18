# Creates the "SLATEWIRE Notifier" scheduled task: every 30 minutes, hidden, only while you are logged in.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
$app = Join-Path $root 'slatewire'
$script = Join-Path $app 'notifier.py'
if (-not (Test-Path $py)) { Write-Host 'SLATEWIRE is not installed yet. Run INSTALL.bat first.'; exit 1 }
if (-not (Test-Path $script)) { Write-Host 'notifier.py was not found next to this file.'; exit 1 }
$action = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '"') -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'SLATEWIRE Notifier' -Action $action -Trigger $trigger -Settings $settings -Description 'SLATEWIRE background news check (IMPORTANT / Catch up notifications)' -Force | Out-Null
Write-Host 'Done. SLATEWIRE will check for news every 30 minutes (first check in about 1 minute) and notify you.'
Write-Host 'It does nothing while the SLATEWIRE window is open. Turn it off any time with DISABLE-NOTIFICATIONS.bat.'
