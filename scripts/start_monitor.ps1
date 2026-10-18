# Inicia el monitor ASR en segundo plano.
# Uso: scripts\start_monitor.ps1 -CaseId <uuid> [-Interval 60]
param(
    [string]$CaseId = $env:JUDICIAL_CASE_ID,
    [int]$Interval = 60
)
if (-not $CaseId) { throw "Pasa -CaseId <uuid> o define JUDICIAL_CASE_ID" }
$env:JUDICIAL_CASE_ID = $CaseId
$logFile = "$env:LOCALAPPDATA\Temp\opencode\monitor_asr.log"
New-Item -ItemType Directory -Path (Split-Path $logFile) -Force | Out-Null

# Matar instancias previas del monitor (launcher + intérprete).
Get-WmiObject Win32_Process -Filter "name like 'python%.exe'" | Where-Object { $_.CommandLine -like '*monitor_asr.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# La contraseña se toma de .env (BOOTSTRAP_ADMIN_PASSWORD) o de JUDICIAL_ADMIN_PASSWORD.
$proc = Start-Process -FilePath ".venv\Scripts\python.exe" `
    -ArgumentList "scripts/monitor_asr.py --watch --repair --interval $Interval" `
    -WorkingDirectory "$PSScriptRoot\.." `
    -WindowStyle Hidden -PassThru

Write-Host "Monitor iniciado (PID: $($proc.Id)). Log: $logFile"
