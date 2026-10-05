# Migrar los datos (tu expediente) a GCP

Cómo pasar **toda** la instancia local (documentos, páginas OCR, transcripciones ASR, **pgvector**, **grafo**,
citas y los **archivos originales**) al VPS de GCP.

> ⚠️ **Estos son datos judiciales reales.** Muévelos por un canal **privado** (scp/rsync/gcloud/GCS) y
> **NUNCA** los subas a GitHub ni los hornees en la imagen Docker. El `.gitignore` ya excluye `backup/`.

## Qué se migra
- **Base de datos** (`pg_dump`): organizaciones, usuarios, casos, documentos, `document_pages` (+ confianza OCR),
  `document_ocr_versions`, `media`, `transcript_segments` (ASR + hablantes), `chunks` con **embeddings pgvector**,
  `graph_nodes`/`graph_edges`, claims/facts/events/citations/evidence/reviews/audit, etc.
- **Originales** (`var/storage`): PDFs, imágenes y videos tal como se subieron (fuente de verdad).
- Los **embeddings viajan dentro del dump**: no hay que reindexar. (Si cambias `EMBEDDING_MODEL`/`EMBEDDING_DIMENSIONS`,
  sí habría que reindexar.)

## Paso 1 — Exportar (en tu máquina local)
```bash
bash scripts/export_data.sh                 # -> backup/judicial-<fecha>/
# o: bash scripts/export_data.sh mi-ruta     # carpeta destino
```
Genera `judicial.dump` (formato `pg_dump -Fc`), `storage.tgz` y `manifest.txt`. Son varios GB.

## Paso 2 — Transferir al VPS (privado)
Elige una:
```bash
# A) gcloud (recomendado si usas GCP)
gcloud compute scp --recurse backup/judicial-<fecha> USUARIO@IP_VPS:/home/USUARIO/

# B) scp/rsync
rsync -avP backup/judicial-<fecha>/ USUARIO@IP_VPS:/home/USUARIO/judicial-data/

# C) Bucket de GCS
gsutil -m cp -r backup/judicial-<fecha> gs://mi-bucket/judicial-data/
#   y en el VPS:  gsutil -m cp -r gs://mi-bucket/judicial-data ./judicial-data
```

## Paso 3 — Importar (en el VPS, con el stack levantado)
```bash
cd judicial-ao
git pull                      # asegúrate de tener los scripts y las migraciones al día
bash scripts/deploy.sh        # levanta imágenes + migra (crea roles y esquema)
bash scripts/import_data.sh /home/USUARIO/judicial-data/judicial-<fecha>
```
`import_data.sh` **recrea la base de datos** y restaura el volcado, restaura `var/storage`, vuelve a levantar
los servicios y verifica migraciones. **Úsalo en una instancia nueva/vacía** (reemplaza los datos actuales).

## Paso 4 — Verificar
- Inicia sesión, abre el proceso y un PDF (debe verse el original + OCR).
- Pregunta al chat algo del expediente: debe citar documento+página o video+minuto.
- `docker compose ps` (todo `healthy`).

## ¿Y "que al construir el Docker siembre los datos"?
**No se debe** meter datos reales en la imagen: sería una fuga + una imagen de varios GB + el CI no lo soporta.
Formas correctas de dejar un VPS nuevo con los datos:

1. **Manual (simple y segura):** tras `deploy.sh`, corre `bash scripts/import_data.sh <dump>` una vez. (Pasos 1–3.)
2. **Semiautomático con un bucket:** subes `judicial.dump` + `storage.tgz` a un bucket privado y en el VPS haces
   un `scripts/fetch_and_import.sh` que los descarga e importa. (Puedo creártelo.)
3. **Auto-seed en el primer arranque sin hornear datos:** montar el dump como **volumen** y un entrypoint que,
   si la BD está vacía, ejecute `import_data.sh`. La imagen queda limpia; el dato vive en el volumen privado.

## Alternativa: solo un expediente (no toda la instancia)
`export_data.sh` migra **toda** la base. Si más adelante quieres pasar **un único expediente** entre dos
instancias que ya tienen datos, se necesita un export/import por caso (documentos + páginas + media + segmentos +
chunks + grafo + citas, preservando FK). Es más delicado; dime y lo implemento como `export_case.py`/`import_case.py`.

## Notas
- Mantén el mismo `STORAGE_BACKEND` (por defecto `local`) y `EMBEDDING_DIMENSIONS` entre origen y destino.
- Si usas `STORAGE_BACKEND=s3`/GCS, los originales ya están en el bucket: entonces basta con el `pg_dump`.
- Los roles `DB_OWNER_USER`/`DB_APP_USER` deben existir en el destino (los crea el bootstrap); el volcado conserva
  propietarios y permisos (RLS/grants).
