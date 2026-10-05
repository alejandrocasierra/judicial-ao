# Wrapper para ejecutar/reanudar process_all_pdfs.py de forma robusta.
# Se asegura de que solo haya una instancia corriendo usando un lock file.
param(
    [string]$CaseId = "182a09e0-deeb-4918-80f6-19cf350b4087",
    [string]$OrgId = "b4e6d687-89b0-4b79-a0cc-69bd3532a7e9",
    [string]$UserId = "fc7ced6f-58e9-46c5-aa7c-b155c424c0fd"
)

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "scripts\process_all_pdfs.py"
$LogFile = Join-Path $Root "var\process_all_pdfs.log"
$PidFile = Join-Path $Root "var\process_all_pdfs.pid"
$LockFile = Join-Path $Root "var\process_all_pdfs.lock"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts WRAPPER $msg"
    Write-Host $line
    $line | Out-File -FilePath $LogFile -Append -Encoding utf8
}

# Adquiere lock file exclusivo con timeout de 5 segundos.
$lock = $null
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 5) {
    try {
        $lock = [System.IO.File]::Open($LockFile, 'Create', 'Write', 'None')
        break
    } catch {
        Start-Sleep -Milliseconds 200
    }
}
if ($lock -eq $null) {
    Write-Log "No se pudo adquirir lock. Otra instancia está corriendo. Saliendo."
    exit 0
}

# Si ya hay un proceso vivo, no hace nada.
if (Test-Path $PidFile) {
    $oldPid = Get-Content $PidFile -Raw -Encoding utf8
    try {
        $proc = Get-Process -Id $oldPid -ErrorAction Stop
        Write-Log "Proceso existente vivo PID $oldPid. Saliendo."
        $lock.Close()
        exit 0
    } catch {
        Write-Log "PID $oldPid no existe. Limpiando pid file."
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Log "Iniciando batch..."
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $VenvPython
$psi.Arguments = "$Script --case-id $CaseId --org-id $OrgId --user-id $UserId"
$psi.WorkingDirectory = $Root
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
$proc.Id | Set-Content -Path $PidFile -Encoding utf8

# Redirige salida al log en background
Start-Job -ScriptBlock {
    param($p, $log)
    while (-not $p.HasExited) {
        $line = $p.StandardOutput.ReadLine()
        if ($line -ne $null) { "$line" | Out-File -FilePath $log -Append -Encoding utf8 }
    }
    $p.StandardOutput.ReadToEnd() | Out-File -FilePath $log -Append -Encoding utf8
    $p.StandardError.ReadToEnd() | Out-File -FilePath $log -Append -Encoding utf8
} -ArgumentList $proc, $LogFile | Out-Null

Write-Log "Batch iniciado PID $($proc.Id). Esperando..."
$proc.WaitForExit()
Write-Log "Batch terminó con código $($proc.ExitCode)"

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
$lock.Close()
