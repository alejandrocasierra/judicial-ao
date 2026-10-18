# Detiene el monitor ASR.
Get-WmiObject Win32_Process -Filter "name like 'python%.exe'" | Where-Object { $_.CommandLine -like '*monitor_asr.py*' } | ForEach-Object {
    Write-Host "Deteniendo monitor PID $($_.ProcessId)..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item -Path "$env:LOCALAPPDATA\Temp\opencode\monitor_asr.lock" -Force -ErrorAction SilentlyContinue
Write-Host "Monitor detenido."
