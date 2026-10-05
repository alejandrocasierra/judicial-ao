# Supervisor para process_all_pdfs.py
# Reinicia el batch si se cae, hasta un máximo de intentos.
param(
    [string]$CaseId = "182a09e0-deeb-4918-80f6-19cf350b4087",
    [string]$OrgId = "b4e6d687-89b0-4b79-a0cc-69bd3532a7e9",
    [string]$UserId = "fc7ced6f-58e9-46c5-aa7c-b155c424c0fd",
    [int]$MaxRestarts = 5,
    [int]$CheckIntervalSeconds = 60
)

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "scripts\process_all_pdfs.py"
$LogFile = Join-Path $Root "var\process_all_pdfs.log"
$SupervisorLog = Join-Path $Root "var\process_all_pdfs.supervisor.log"
$PidFile = Join-Path $Root "var\process_all_pdfs.pid"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts SUPERVISOR $msg"
    Write-Host $line
    $line | Out-File -FilePath $SupervisorLog -Append -Encoding utf8
}

function Start-Batch {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $VenvPython
    $psi.Arguments = "$Script --case-id $CaseId --org-id $OrgId --user-id $UserId"
    $psi.WorkingDirectory = $Root
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    # Redirige stdout/stderr al log file en background
    Start-Job -ScriptBlock {
        param($proc, $log)
        while (-not $proc.HasExited) {
            $line = $proc.StandardOutput.ReadLine()
            if ($line -ne $null) { "$line" | Out-File -FilePath $log -Append -Encoding utf8 }
        }
        $remaining = $proc.StandardOutput.ReadToEnd()
        if ($remaining) { $remaining | Out-File -FilePath $log -Append -Encoding utf8 }
        $err = $proc.StandardError.ReadToEnd()
        if ($err) { $err | Out-File -FilePath $log -Append -Encoding utf8 }
    } -ArgumentList $proc, $LogFile | Out-Null
    $proc.Id | Set-Content -Path $PidFile -Encoding utf8
    return $proc
}

$restartCount = 0
while ($restartCount -le $MaxRestarts) {
    Write-Log "Iniciando batch (intento $($restartCount + 1)/$($MaxRestarts + 1))"
    $proc = Start-Batch
    Write-Log "PID $($proc.Id)"
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds $CheckIntervalSeconds
    }
    Write-Log "Proceso terminó con código $($proc.ExitCode)"

    $progressFile = Join-Path $Root "var\process_all_pdfs.json"
    $completedCount = 0
    if (Test-Path $progressFile) {
        $progress = Get-Content $progressFile -Raw -Encoding utf8 | ConvertFrom-Json
        $completedCount = ($progress.completed | Measure-Object).Count
        Write-Log "Progreso actual: $completedCount documentos completados"
        if ($completedCount -eq 202) {
            Write-Log "Batch completado. Saliendo."
            break
        }
    }

    $restartCount++
    if ($restartCount -le $MaxRestarts) {
        Write-Log "Reiniciando en 10 segundos..."
        Start-Sleep -Seconds 10
    } else {
        Write-Log "Máximo de reinicios alcanzado. Batch abortado."
    }
}
