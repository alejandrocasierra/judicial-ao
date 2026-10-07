# Imagen de la API: multi-stage, usuario no root, sin secretos embebidos (todo por variables de entorno).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

FROM base AS deps
# PyTorch SOLO CPU: evita las librerías de GPU de NVIDIA (~7 GB) que no se usan
# cuando ASR_DEVICE=cpu. Se instala ANTES de base.txt para que pip lo dé por
# satisfecho y no baje la variante CUDA desde PyPI.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio torchvision
COPY requirements/base.txt requirements/base.txt
RUN pip install -r requirements/base.txt
# Dependencias del servidor MCP en su propia capa: añadir/actualizar el SDK no
# invalida la capa pesada de base.txt (torch, docling, pyannote…).
COPY requirements/mcp.txt requirements/mcp.txt
RUN pip install -r requirements/mcp.txt
# Backend Google Cloud Storage (capa aparte).
COPY requirements/gcs.txt requirements/gcs.txt
RUN pip install -r requirements/gcs.txt

FROM base AS runtime
COPY --from=deps /usr/local /usr/local
# FFmpeg "full" con libs compartidas: requerido por torchcodec (pyannote.audio 4)
# para decodificar audio en la diarización. Debian bookworm provee FFmpeg 5.1,
# versión soportada por torchcodec. También usado para extraer audio/video frames.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system app && useradd --system --gid app --home /srv app
# Cachés de modelos (whisper, huggingface) deben ser escribibles por el usuario app.
RUN mkdir -p /srv/.cache /srv/var/hf-cache /srv/var/whisper-cache \
    && chown -R app:app /srv/.cache /srv/var/hf-cache /srv/var/whisper-cache
COPY apps/api apps/api
COPY apps/mcp_server apps/mcp_server
COPY config config
COPY packages packages
COPY scripts scripts
ENV REPO_ROOT=/srv
RUN mkdir -p /srv/var && chown -R app:app /srv/var
USER app
WORKDIR /srv/apps/api
HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=5 CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"API_PORT\"]}/health')" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$API_HOST\" --port \"$API_PORT\" --proxy-headers"]
