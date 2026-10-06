"""DTOs con validación estricta: extra='forbid' bloquea mass-assignment
(p.ej. enviar organization_id, status o legal_hold en el cuerpo)."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _no_control(v: str | None) -> str | None:
    if v is not None and any(ord(ch) < 32 and ch not in "\n\t" for ch in v):
        raise ValueError("control characters not allowed")
    return v


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")


def _email(v: str) -> str:
    # Validación sintáctica propia: email-validator rechaza TLD reservados
    # (.test/.example) que usamos deliberadamente en semillas (RFC 2606).
    v = v.strip().lower()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("invalid email")
    return v


class LoginIn(Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    _e = field_validator("email")(_email)


class RefreshIn(Strict):
    refresh_token: str = Field(min_length=20, max_length=4096)


class CaseCreate(Strict):
    jurisdiction: str = Field(min_length=2, max_length=20, pattern=r"^[a-z0-9_\-]+$")
    case_number: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=3, max_length=300)
    court: str | None = Field(default=None, max_length=200)
    chamber: str | None = Field(default=None, max_length=200)
    external_reference: str | None = Field(default=None, max_length=120)
    language: Literal["es", "en"]

    _v = field_validator("case_number", "title", "court", "chamber", "external_reference")(_no_control)


class CasePatch(Strict):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=3, max_length=300)
    court: str | None = Field(default=None, max_length=200)
    chamber: str | None = Field(default=None, max_length=200)
    status: Literal["CREATED", "UPLOADING", "INGESTING", "PROCESSING", "PARTIALLY_READY", "READY_FOR_REVIEW",
                    "REVIEWING", "READY", "FAILED", "ARCHIVED"] | None = None

    _v = field_validator("title", "court", "chamber")(_no_control)


class MemberIn(Strict):
    user_id: UUID
    case_role: Literal["OWNER", "LAWYER", "REVIEWER", "VIEWER"]


class LegalHoldIn(Strict):
    enabled: bool
    reason: str = Field(min_length=5, max_length=500)


class DeletionRequestIn(Strict):
    reason: str = Field(min_length=10, max_length=1000)


class ProcessIn(Strict):
    job_types: list[Literal["document_ocr", "document_classification", "media_asr", "diarization", "legal_extraction",
                            "embedding", "indexing", "graph_build", "file_ingest", "xlsx_ingest"]] = Field(min_length=1, max_length=10)


class FolderCreate(Strict):
    name: str = Field(min_length=1, max_length=200)
    parent_id: UUID | None = None

    _v = field_validator("name")(_no_control)


class FolderPatch(Strict):
    name: str = Field(min_length=1, max_length=200)

    _v = field_validator("name")(_no_control)


class CaseFilePatch(Strict):
    filename: str = Field(min_length=1, max_length=200)

    _v = field_validator("filename")(_no_control)


class UploadPresignIn(Strict):
    """Petición de URL prefirmada para subir un archivo grande DIRECTO al storage."""
    filename: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    content_type: str | None = Field(default=None, max_length=200)

    _f = field_validator("filename", "content_type")(_no_control)


class UploadCompleteIn(Strict):
    """Registro de un archivo ya subido por URL prefirmada."""
    filename: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    mime_type: str | None = Field(default=None, max_length=200)
    folder_id: UUID | None = None
    ocr_mode: Literal["basico", "document_ai"] | None = None

    _f = field_validator("filename", "mime_type")(_no_control)


class SpeakerCreate(Strict):
    """Alta de un hablante manual (no detectado por la diarización)."""
    display_name: str = Field(min_length=1, max_length=200)
    speaker_role: str | None = Field(default=None, max_length=100)
    resolved_party_id: UUID | None = None

    _v = field_validator("display_name", "speaker_role")(_no_control)


class SpeakerPatch(Strict):
    """Edición del nombre, el rol y/o la parte asociada de un hablante."""
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_role: str | None = Field(default=None, max_length=100)
    resolved_party_id: UUID | None = None

    _v = field_validator("display_name", "speaker_role")(_no_control)


class SpeakerMergeIn(Strict):
    """Fusiona dos hablantes que son la misma persona (dos clusters de diarización)."""
    keep_speaker_id: UUID
    merge_speaker_id: UUID


class SpeakerRoleItem(Strict):
    speaker_id: UUID
    speaker_role: str = Field(min_length=1, max_length=100)
    resolved_party_id: UUID | None = None


class SpeakerRoleAssign(Strict):
    """Asignación de rol (y parte opcional) a varios hablantes de una vez."""
    assignments: list[SpeakerRoleItem] = Field(min_length=1, max_length=200)


class ReviewIn(Strict):
    entity_type: Literal["claim", "fact", "speaker", "contradiction"]
    action: Literal["ACCEPT", "EDIT", "REJECT", "FLAG"]
    expected_version: int = Field(ge=1)
    changes: dict = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=1000)


class QueryAttachment(Strict):
    """Un "@" real del chat: el archivo que el usuario adjuntó a su pregunta."""
    kind: Literal["document", "media"]
    id: UUID
    name: str | None = Field(default=None, max_length=500)

    _n = field_validator("name")(_no_control)


class ChatSessionIn(Strict):
    """Crear una sesión de chat en un expediente (agente/modelo recordados)."""
    agent_id: UUID | None = None
    model_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)

    _t = field_validator("title")(_no_control)


class ChatMessageIn(Strict):
    """Enviar un mensaje en una sesión: corre el agente con historial y adjuntos.
    min_length=2 para admitir respuestas cortas de confirmación ("sí", "no")."""
    content: str = Field(min_length=2, max_length=8000)
    attachments: list[QueryAttachment] = Field(default_factory=list, max_length=10)
    strategy: Literal["agent", "rag"] = "agent"

    _c = field_validator("content")(_no_control)


class QueryIn(Strict):
    question: str = Field(min_length=3)
    mode: Literal["fact_lookup", "evidence_lookup", "timeline", "person", "document", "testimony", "contradiction",
                  "legal_rule", "summary", "comparative", "investigation"] = "fact_lookup"
    strategy: Literal["rag", "agent"] = "rag"
    agent_id: UUID | None = None  # agente configurado que dirige la consulta
    model_id: UUID | None = None  # modelo configurado en /dashboard/models
    attachments: list[QueryAttachment] = Field(default_factory=list, max_length=10)  # los "@" del chat

    _v = field_validator("question")(_no_control)


class AdminUserCreate(Strict):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=2, max_length=200)
    org_role: Literal["ORG_ADMIN", "CASE_MANAGER", "LAWYER", "REVIEWER", "ANALYST", "READ_ONLY"]
    locale: Literal["es", "en"] = "es"

    _e = field_validator("email")(_email)
    _n = field_validator("full_name")(_no_control)


class AdminUserPatch(Strict):
    expected_version: int = Field(ge=1)
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    org_role: Literal["ORG_ADMIN", "CASE_MANAGER", "LAWYER", "REVIEWER", "ANALYST", "READ_ONLY"] | None = None
    locale: Literal["es", "en"] | None = None
    is_active: bool | None = None

    _n = field_validator("full_name")(_no_control)


class SmtpSettingsPatch(Strict):
    from_name: str = Field(default="", max_length=100)
    from_email: str = Field(default="", max_length=254)
    server: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["STARTTLS", "SSL/TLS", "NONE"] = "STARTTLS"
    username: str = Field(default="", max_length=254)
    password: str | None = Field(default=None, max_length=256)
    cc_emails: str = Field(default="", max_length=500)  # CSV de emails


class AgentIn(Strict):
    name: str = Field(min_length=1, max_length=150)
    system_prompt: str = Field(default="", max_length=20000)
    skills: list[str] = Field(default_factory=list, max_length=50)

    _n = field_validator("name")(_no_control)


class AgentPatch(Strict):
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    system_prompt: str | None = Field(default=None, max_length=20000)
    skills: list[str] | None = Field(default=None, max_length=50)

    _n = field_validator("name")(_no_control)


class SkillIn(Strict):
    name: str = Field(min_length=1, max_length=150)
    system_prompt: str = Field(default="", max_length=20000)

    _n = field_validator("name")(_no_control)


class SkillPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    system_prompt: str | None = Field(default=None, max_length=20000)

    _n = field_validator("name")(_no_control)


class AiModelIn(Strict):
    provider: Literal["anthropic", "openai", "gemini", "kimi", "deepseek", "custom"]
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=400)
    api_base_url: str | None = Field(default=None, max_length=500)  # override opcional (proxy/VPS/región)
    is_default: bool = False
    ocr_enabled: bool = False  # único modelo con OCR activo por organización
    asr_enabled: bool = False  # único modelo con ASR activo por organización

    _m = field_validator("model_name")(_no_control)
    _u = field_validator("api_base_url")(_no_control)


class AiModelPatch(Strict):
    provider: Literal["anthropic", "openai", "gemini", "kimi", "deepseek", "custom"] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=400)
    api_base_url: str | None = Field(default=None, max_length=500)
    is_default: bool | None = None
    ocr_enabled: bool | None = None
    asr_enabled: bool | None = None

    _m = field_validator("model_name")(_no_control)
    _u = field_validator("api_base_url")(_no_control)


class ModelCatalogIn(Strict):
    """Consulta del catálogo dinámico de modelos de un proveedor."""
    provider: Literal["anthropic", "openai", "gemini", "kimi", "deepseek", "custom"]
    api_key: str | None = Field(default=None, max_length=400)
    api_base_url: str | None = Field(default=None, max_length=500)

    _u = field_validator("api_base_url")(_no_control)


class RoleIn(Strict):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=100)

    _n = field_validator("name", "description")(_no_control)


class RolePatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = Field(default=None, max_length=100)

    _n = field_validator("name", "description")(_no_control)


class DocumentPagePatch(Strict):
    text: str = Field(min_length=1, max_length=200000)
    # Modo OCR que se está editando en el visor. Si falta, se edita la página "actual".
    mode: Literal["basico", "document_ai"] | None = None
    expected_version: int | None = Field(default=None, ge=1)

    _t = field_validator("text")(_no_control)


class TranscriptSegmentPatch(Strict):
    """Corrección humana de un segmento: el texto y/o quién lo dijo.

    `speaker_id` sólo se aplica si viene en el cuerpo (incluso como `null`, para
    desasignar el hablante). Así se puede corregir sólo el texto sin tocar a quién
    se atribuye, y viceversa.
    """
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    speaker_id: UUID | None = None

    _t = field_validator("text")(_no_control)

    @model_validator(mode="after")
    def _require_some_change(self):
        if self.text is None and "speaker_id" not in self.model_fields_set:
            raise ValueError("text or speaker_id required")
        return self
