"""Configuración 100% por variables de entorno. Ningún campo tiene valor por
defecto: si falta una variable, la app NO arranca (fail-fast).
Configuration is 100% environment-driven; no field has a default."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDERS = {"__GENERATE__", "__SET_ME__", ""}


def _csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def repo_root() -> Path:
    return Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[4]))


def env_file() -> str:
    p = Path(os.environ.get("ENV_FILE", ".env"))
    return str(p if p.is_absolute() else repo_root() / p)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=env_file(), extra="ignore", case_sensitive=True)

    APP_ENV: Literal["development", "test", "staging", "production"]
    APP_NAME: str
    API_PREFIX: str
    # Servidor MCP (Chat IA): streamable HTTP, mismo JWT, RLS por llamada
    MCP_SERVER_ENABLED: bool
    MCP_SERVER_HOST: str
    MCP_SERVER_PORT: int = Field(gt=0)
    # Hosts permitidos (protección DNS-rebinding del SDK MCP), separados por coma
    MCP_ALLOWED_HOSTS: str
    # Chat multi-turn: turnos de historial que recibe el agente por mensaje
    CHAT_HISTORY_TURNS: int = Field(gt=0, le=40)
    LOG_LEVEL: str
    DEFAULT_LOCALE: str
    SUPPORTED_LOCALES: str
    I18N_DIR: str
    RBAC_POLICY_FILE: str
    JURISDICTIONS_DIR: str
    CORS_ALLOWED_ORIGINS: str
    PIPELINE_VERSION: str
    SCHEMA_VERSION: str
    WEB_BASE_URL: str
    SMTP_FROM_NAME: str
    SMTP_FROM_EMAIL: str
    SMTP_SERVER: str
    SMTP_PORT: int = Field(gt=0)
    SMTP_SECURITY: str
    SMTP_USERNAME: str
    SMTP_CC_EMAILS: str

    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str
    DB_APP_USER: str
    DB_APP_PASSWORD: str
    DB_POOL_SIZE: int = Field(gt=0)
    FTS_CONFIG: str
    EMBEDDING_DIMENSIONS: int = Field(gt=0)

    REDIS_URL: str

    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    CELERY_TASK_ALWAYS_EAGER: bool
    JOB_STALE_MINUTES: int = Field(gt=0)
    JOB_TIME_LIMIT_SECONDS: int = Field(gt=0)
    # Límite de tiempo de las tareas de MEDIOS (ASR/diarización). Mucho mayor que el
    # general: diarizar horas de audio con pyannote en CPU tarda mucho. Estos jobs NO
    # pasan por el sweeper de huérfanos (migración 0037), así que no exigen respetar
    # JOB_STALE_MINUTES.
    MEDIA_JOB_TIME_LIMIT_SECONDS: int = Field(default=21600, gt=0)
    # Al arrancar el worker, reencola los jobs RUNNING más antiguos que esto (recupera
    # huérfanos tras un reinicio). Con varios workers en paralelo, súbelo.
    STARTUP_REAP_MINUTES: int = 1

    JWT_SECRET: str
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"]
    JWT_ISSUER: str
    JWT_AUDIENCE: str
    ACCESS_TOKEN_TTL_SECONDS: int = Field(gt=0)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(gt=0)
    AUTH_MAX_FAILED_ATTEMPTS: int = Field(gt=0)
    AUTH_LOCKOUT_SECONDS: int = Field(gt=0)
    PASSWORD_MIN_LENGTH: int = Field(ge=8)

    RATE_LIMIT_BACKEND: Literal["memory", "redis"]
    RATE_LIMIT_LOGIN_PER_MINUTE: int = Field(gt=0)
    RATE_LIMIT_QUERIES_PER_MINUTE: int = Field(gt=0)
    RATE_LIMIT_UPLOADS_PER_MINUTE: int = Field(gt=0)
    RATE_LIMIT_ORG_QUERIES_PER_MINUTE: int = Field(gt=0)
    RATE_LIMIT_ORG_UPLOADS_PER_MINUTE: int = Field(gt=0)

    STORAGE_BACKEND: Literal["local", "s3", "gcs"]
    STORAGE_LOCAL_ROOT: str
    S3_ENDPOINT_URL: str
    S3_BUCKET: str
    S3_REGION: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    # Prefijo (carpeta) dentro del bucket: p. ej. "judicial-ai/prod". Vacío = raíz.
    # Se usa también como bucket/carpeta para GCS (STORAGE_BACKEND=gcs).
    S3_PREFIX: str
    # Cifrado en reposo del objeto: "AES256" (S3/MinIO) o vacío (Google Cloud Storage lo cifra por defecto).
    S3_SSE: str

    UPLOAD_MAX_BYTES_DOCUMENT: int = Field(gt=0)
    UPLOAD_MAX_BYTES_MEDIA: int = Field(gt=0)
    UPLOAD_ALLOWED_DOCUMENT_TYPES: str
    UPLOAD_ALLOWED_MEDIA_TYPES: str
    MALWARE_SCANNER: Literal["basic", "clamav"]
    CLAMAV_HOST: str
    CLAMAV_PORT: int

    OCR_CONFIDENCE_THRESHOLD: float = Field(ge=0, le=1)
    OCR_PROVIDER: Literal["tesseract", "docling", "docling_layout", "docling_latin", "handwriting", "document_ai", "fake"]
    OCR_HANDWRITING_MODEL: str
    OCR_TESSERACT_CMD: str
    OCR_TESSERACT_TESSDATA_DIR: str
    OCR_TESSERACT_LANG: str
    OCR_DPI: int = Field(gt=0)
    OCR_PREPROCESS: bool
    # Extracción automática de PARTES al terminar OCR/ASR (encabezados + hablantes).
    AUTO_EXTRACT_PARTIES: bool = True
    # Google Document AI (vacío = deshabilitado)
    GOOGLE_CLOUD_PROJECT: str
    GOOGLE_CLOUD_LOCATION: str
    GOOGLE_DOCUMENT_AI_PROCESSOR_ID: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    # Alcance OAuth2 y plantilla del endpoint :process (placeholders {location}/{project}/{processor})
    GOOGLE_CLOUD_SCOPE: str
    GOOGLE_DOCUMENT_AI_ENDPOINT_TEMPLATE: str
    ASR_CONFIDENCE_THRESHOLD: float = Field(ge=0, le=1)
    ASR_PROVIDER: Literal["whisper", "fake"]
    ASR_MODEL: str
    ASR_DEVICE: Literal["cpu", "cuda", "auto"]
    ASR_COMPUTE_TYPE: Literal["int8", "float16", "float32"]
    ASR_BEAM_SIZE: int = Field(ge=1)
    ASR_BEST_OF: int = Field(ge=1)
    # ASR por tramos: faster-whisper consume memoria proporcional a la duración del audio
    # (~55 MB/min). Un audio de 2 h en una sola pasada ~8 GB (OOM). Se trocea en tramos
    # de N segundos (0 = sin trocear) para acotar el pico (~1 GB con 10 min).
    ASR_CHUNK_SECONDS: int = Field(default=600, ge=0)
    # Hilos de CPU para faster-whisper. 0 = automático (todos los núcleos). En VPS
    # pequeñas conviene fijar 1-2 para no saturar la máquina (deja CPU al API/DB).
    ASR_CPU_THREADS: int = Field(default=0, ge=0)
    # Diarización POR TRAMOS (audios largos): pyannote sobre el audio completo es
    # lentísimo y agota tiempo/memoria. Se parte en tramos de N segundos (con solape
    # para no perder hablantes en los cortes) y se unifican hablantes por embeddings.
    ASR_DIARIZATION_CHUNK_SECONDS: int = Field(default=900, ge=0)
    ASR_DIARIZATION_OVERLAP_SECONDS: int = Field(default=10, ge=0)
    # Distancia coseno máxima para considerar que dos voces son el MISMO hablante.
    # Más alto = fusiona más (súbelo si la misma persona sale partida; bájalo si une a
    # personas distintas). 0.5 es el equilibrado por defecto.
    ASR_DIARIZATION_CLUSTER_THRESHOLD: float = Field(default=0.5, ge=0, le=2)
    ASR_VAD_FILTER: bool
    ASR_VAD_PARAMETERS: dict | None
    ASR_MIN_SPEECH_DURATION_MS: int = Field(ge=0)
    ASR_DIARIZATION_TOKEN: str
    FFMPEG_PATH: str
    HF_TOKEN: str

    LLM_PROVIDER: Literal["fake", "anthropic", "openai"]
    LLM_MODEL: str
    LLM_API_KEY: str
    LLM_API_BASE_URL: str
    LLM_API_VERSION: str
    # Base URL (compatible con OpenAI Chat Completions) de cada proveedor del dashboard
    OPENAI_BASE_URL: str
    DEEPSEEK_BASE_URL: str
    KIMI_BASE_URL: str
    GEMINI_BASE_URL: str
    LLM_MAX_TOKENS: int = Field(gt=0)
    LLM_TIMEOUT_SECONDS: int = Field(gt=0)
    # JSON opcional: {"task": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "..."}}.
    # Si falta una clave hereda del LLM default. Vacío/null = sin routing.
    LLM_ROUTING: dict | None = Field()
    QUESTION_MAX_CHARS: int = Field(gt=0)
    RETRIEVAL_TOP_K: int = Field(gt=0, le=50)
    RETRIEVAL_RRF_K: int = Field(gt=0)
    RETRIEVAL_SNIPPET_MAX_CHARS: int = Field(gt=100)
    ANSWER_MIN_GROUNDING_OVERLAP: float = Field(ge=0.0, le=1.0)
    ABSTENTION_MIN_TERM_COVERAGE: float = Field(ge=0.0, le=1.0)
    PROMPTS_DIR: str

    EMBEDDING_PROVIDER: Literal["fake", "openai", "sentence-transformers"]
    EMBEDDING_MODEL: str
    EMBEDDING_API_KEY: str
    EMBEDDING_API_BASE_URL: str
    EMBEDDING_BATCH_SIZE: int = Field(gt=0)
    EMBEDDING_MAX_CHARS: int = Field(gt=0)

    CASE_MAX_PROCESSING_COST: float = Field(gt=0)
    CASE_MAX_LLM_TOKENS: int = Field(gt=0)
    CASE_MAX_MEDIA_HOURS: float = Field(gt=0)

    @field_validator("ASR_VAD_PARAMETERS", mode="before")
    @classmethod
    def _asr_vad_parameters(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else None
            except Exception as exc:
                raise ValueError("ASR_VAD_PARAMETERS must be a JSON object or empty") from exc
        raise ValueError("ASR_VAD_PARAMETERS must be a dict or None")

    @field_validator("LLM_ROUTING", mode="before")
    @classmethod
    def _llm_routing(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            import json
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else None
            except Exception as exc:
                raise ValueError("LLM_ROUTING must be a JSON object or empty") from exc
        raise ValueError("LLM_ROUTING must be a dict or None")

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_strong(cls, v: str) -> str:
        if v in PLACEHOLDERS or len(v) < 32:
            raise ValueError("JWT_SECRET must be set and >= 32 chars (run scripts/gen_env.py)")
        return v

    @model_validator(mode="after")
    def _guards(self):
        # En TODOS los entornos: el sweeper nunca debe barrer un job legítimo en ejecución
        if self.JOB_STALE_MINUTES * 60 <= self.JOB_TIME_LIMIT_SECONDS:
            raise ValueError("JOB_STALE_MINUTES*60 must be > JOB_TIME_LIMIT_SECONDS (at least 2x)")
        if self.APP_ENV == "production":
            if self.LLM_PROVIDER == "fake":
                raise ValueError("LLM_PROVIDER=fake is not allowed in production")
            if self.MALWARE_SCANNER == "basic":
                raise ValueError("MALWARE_SCANNER=basic is not allowed in production")
            if self.RATE_LIMIT_BACKEND == "memory":
                raise ValueError("RATE_LIMIT_BACKEND=memory is not allowed in production")
            if self.CELERY_TASK_ALWAYS_EAGER:
                raise ValueError("CELERY_TASK_ALWAYS_EAGER=true is not allowed in production")
        if self.LLM_PROVIDER != "fake" and (self.LLM_API_KEY in PLACEHOLDERS or self.LLM_MODEL in PLACEHOLDERS):
            raise ValueError("LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER is not 'fake'")
        if self.DEFAULT_LOCALE not in self.locales:
            raise ValueError("DEFAULT_LOCALE must be in SUPPORTED_LOCALES")
        if self.DB_APP_PASSWORD in PLACEHOLDERS:
            raise ValueError("DB_APP_PASSWORD must be set (run scripts/gen_env.py)")
        return self

    @property
    def locales(self) -> list[str]:
        return _csv(self.SUPPORTED_LOCALES)

    @property
    def cors_origins(self) -> list[str]:
        return _csv(self.CORS_ALLOWED_ORIGINS)

    @property
    def allowed_document_types(self) -> set[str]:
        return set(_csv(self.UPLOAD_ALLOWED_DOCUMENT_TYPES))

    @property
    def allowed_media_types(self) -> set[str]:
        return set(_csv(self.UPLOAD_ALLOWED_MEDIA_TYPES))

    @property
    def database_url(self) -> str:
        return (f"postgresql+psycopg://{quote(self.DB_APP_USER)}:{quote(self.DB_APP_PASSWORD)}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}")

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else repo_root() / p


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
