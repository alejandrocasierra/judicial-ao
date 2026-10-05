# Arranca el orquestador de OCR en bucle: si el proceso muere, se reinicia solo
# (el script es reanudable por BD, así que continúa donde quedó).
$py = "C:\Users\ale13\OneDrive\Escritorio\judicial-ai\.venv\Scripts\python.exe"
$wd = "C:\Users\ale13\OneDrive\Escritorio\judicial-ai"
Set-Location $wd
while ($true) {
    & $py batch_ocr.py
    Start-Sleep -Seconds 5
}
