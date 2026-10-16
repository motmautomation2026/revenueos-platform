<#
install_worker.ps1
==================

Keeps the brain's worker (python run_for.py work) running on this machine without anyone
starting it: it starts when you sign in to Windows and is started again if it stops.

WHY
---
The platform's "Start execution" button and the approvals given there only do something while
the worker is running. Started by hand, it stops when the window is closed.

WHAT IT DOES
------------
    Registers ONE Windows scheduled task for the current user, "RevenueOS Brain Worker":
        starts at sign-in, restarts every minute if it stops, never runs twice at once.
    The worker's own output goes to logs\worker.log (each customer's steps keep their own logs
    in customers\<key>\logs\).
    Nothing else on the machine is changed. No admin rights are needed.

RUN (in PowerShell, from this folder)
---
    .\install_worker.ps1                    install and start
    .\install_worker.ps1 -Status            is it installed and running?
    .\install_worker.ps1 -Remove            stop and remove
    .\install_worker.ps1 -Python C:\path\to\python.exe     use a specific Python (e.g. a venv's)

BEFORE INSTALLING
-----------------
    - .env is filled in and  python check_system.py  passes.
    - supabase/migrations/0003_project_brain_key.sql has been run once.
    - Run ONE worker per machine. Starting it means: a "Start execution" click in the platform
      makes this machine plan a month (LLM calls) and, after the approvals given in the platform,
      run the tools.
#>

param(
    [switch]$Remove,
    [switch]$Status,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$TaskName = "RevenueOS Brain Worker"
$Brain = $PSScriptRoot

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Status) {
    if (-not $existing) { Write-Host "Not installed."; exit 0 }
    $info = $existing | Get-ScheduledTaskInfo
    Write-Host "Installed. State: $($existing.State). Last started: $($info.LastRunTime)."
    Write-Host "Log: $(Join-Path $Brain 'logs\worker.log')"
    exit 0
}

if ($Remove) {
    if (-not $existing) { Write-Host "Not installed. Nothing to remove."; exit 0 }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed. A step that was already running finishes on its own."
    exit 0
}

$command = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $command) { Write-Host "STOPPED: '$Python' was not found. Give the full path with -Python."; exit 1 }
$pythonPath = $command.Source
if (-not (Test-Path (Join-Path $Brain "run_for.py"))) { Write-Host "STOPPED: run_for.py is not in $Brain."; exit 1 }
if (-not (Test-Path (Join-Path $Brain ".env"))) { Write-Host "STOPPED: $Brain\.env is missing. Fill it in first."; exit 1 }

New-Item -ItemType Directory -Force (Join-Path $Brain "logs") | Out-Null

# UTF-8 and unbuffered, so the log is complete and readable while the worker runs.
$line = "set PYTHONIOENCODING=utf-8&& set PYTHONUNBUFFERED=1&& `"$pythonPath`" run_for.py work >> logs\worker.log 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $line" -WorkingDirectory $Brain
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "RevenueOS brain worker: python run_for.py work (in $Brain)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started: $TaskName"
Write-Host "  Python : $pythonPath"
Write-Host "  Folder : $Brain"
Write-Host "  Log    : $(Join-Path $Brain 'logs\worker.log')"
Write-Host "Check it: .\install_worker.ps1 -Status     Remove it: .\install_worker.ps1 -Remove"
