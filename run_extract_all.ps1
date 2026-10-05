# Extractor masivo con auto-reinicio (reanudable por estado en /tmp).
while ($true) {
    docker exec judicial-ai-worker-1 python /srv/apps/api/batch_extract.py
    Start-Sleep -Seconds 5
}
