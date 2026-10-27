# setup_scheduler.ps1
# ============================================================
# Registers the Shortfundly worker as a Windows Scheduled Task.
# Run this script ONCE in an elevated (Administrator) PowerShell.
#
# Usage:
#   .\setup_scheduler.ps1              # daily at 08:00
#   .\setup_scheduler.ps1 -Hour 6 -Minute 30   # daily at 06:30
#   .\setup_scheduler.ps1 -Remove     # remove the task
# ============================================================

param(
    [int]$Hour = 8,
    [int]$Minute = 0,
    [switch]$Remove
)

$TaskName    = "ShortfundlyWorker"
$Description = "Shortfundly automated college discovery, monitoring and email pipeline"

# Resolve paths relative to this script's location
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$WorkerScript = Join-Path $ProjectDir "worker.py"
$LogFile    = Join-Path $ProjectDir "data\worker.log"

# ---- Remove existing task if requested ----
if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
    } else {
        Write-Host "Task '$TaskName' not found." -ForegroundColor Yellow
    }
    exit 0
}

# ---- Validate prerequisites ----
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python venv not found at: $PythonExe" -ForegroundColor Red
    Write-Host "Create it first:  python -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path $WorkerScript)) {
    Write-Host "ERROR: worker.py not found at: $WorkerScript" -ForegroundColor Red
    exit 1
}

# Ensure log directory exists
$DataDir = Join-Path $ProjectDir "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
}

# ---- Build the scheduled task ----
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m worker --once" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At ([datetime]::Today.AddHours($Hour).AddMinutes($Minute))

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Remove old task if it exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replaced existing task."
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description $Description `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal | Out-Null

Write-Host ""
Write-Host "✅ Scheduled task registered successfully!" -ForegroundColor Green
Write-Host "   Task name : $TaskName"
Write-Host "   Runs      : Daily at $($Hour.ToString('D2')):$($Minute.ToString('D2'))"
Write-Host "   Script    : $WorkerScript"
Write-Host "   Log file  : $LogFile"
Write-Host ""
Write-Host "To run the task immediately:"
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "To remove the task:"
Write-Host "   .\setup_scheduler.ps1 -Remove"
Write-Host ""
Write-Host "To view logs:"
Write-Host "   Get-Content '$LogFile' -Tail 50"
