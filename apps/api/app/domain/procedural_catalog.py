"""Catálogo canónico de subtipos para eventos procesales.

El LLM puede devolver variantes libres (con/sin acentos, guiones, espacios,
preposiciones). Este módulo las normaliza a un repertorio cerrado de subtipos
canónicos antes de persistir en ``events.subtype``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Final


def _normalize_key(raw: str) -> str:
    """Clave de búsqueda agnóstica a puntuación, acentos, mayúsculas o espacios."""
    t = unicodedata.normalize("NFKD", raw.lower().strip())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", t)


# Subtipos canónicos aceptados en events.subtype.
PROCEDURAL_SUBTYPES: Final[frozenset[str]] = frozenset(
    {
        "demanda",
        "contestacion",
        "excepcion",
        "reconvencion",
        "auto",
        "auto_admisorio",
        "auto_de_pruebas",
        "auto_inhibitorio",
        "sentencia",
        "notificacion",
        "notificacion_personal",
        "notificacion_por_estado",
        "notificacion_electronica",
        "emplazamiento",
        "alegato",
        "recurso",
        "apelacion",
        "casacion",
        "queja",
        "revision",
        "nulidad",
        "reposicion",
        "desistimiento",
        "prueba",
        "solicitud_prueba",
        "decreto_prueba",
        "practica_prueba",
        "testimonio",
        "interrogatorio",
        "dictamen",
        "inspeccion_judicial",
        "incorporacion_documental",
        "memorial",
        "tutela",
        "incidente",
        "medida_cautelar",
        "ejecucion",
        "concesion_recurso",
        # Valor de compatibilidad hacia atrás para subtipos no clasificados.
        "otro",
    }
)


# Mapa de variantes/sinónimos a subtipo canónico. La clave se procesa con
# _normalize_key, por lo que "auto admisorio", "auto_admisorio" y
# "auto-admisorio" son equivalentes.
_SUBTYPE_SYNONYMS: Final[dict[str, str]] = {
    # Demanda y respuestas
    "demanda": "demanda",
    "demandas": "demanda",
    "contestacion": "contestacion",
    "contestaciondemanda": "contestacion",
    "contestaciondedemanda": "contestacion",
    "contestaciondelademanda": "contestacion",
    "contestaciondademanda": "contestacion",
    "contestacionlademanda": "contestacion",
    "contestacionadelademanda": "contestacion",
    "excepcion": "excepcion",
    "excepciones": "excepcion",
    "reconvencion": "reconvencion",
    "reconvenciones": "reconvencion",
    # Autos
    "auto": "auto",
    "autos": "auto",
    "autoadmisorio": "auto_admisorio",
    "autoadmisoriodemanda": "auto_admisorio",
    "autoadmitedemanda": "auto_admisorio",
    "autoadmite": "auto_admisorio",
    "autoadmitelademanda": "auto_admisorio",
    "admision": "auto_admisorio",
    "admisiondelademanda": "auto_admisorio",
    "autodepruebas": "auto_de_pruebas",
    "decretodepruebas": "auto_de_pruebas",
    "decretopruebas": "auto_de_pruebas",
    "autopruebas": "auto_de_pruebas",
    "fijacionaudiencia": "auto_de_pruebas",
    "fijaciondelaudiencia": "auto_de_pruebas",
    "audiencia": "auto_de_pruebas",
    "autoinhibitorio": "auto_inhibitorio",
    "inhibitorio": "auto_inhibitorio",
    # Decisiones
    "sentencia": "sentencia",
    "sentencias": "sentencia",
    # Notificaciones
    "notificacion": "notificacion",
    "notificaciones": "notificacion",
    "notificacionpersonal": "notificacion_personal",
    "notificacionporestado": "notificacion_por_estado",
    "notificacionporaviso": "notificacion_por_estado",
    "notificacionelectronica": "notificacion_electronica",
    "notificaciondigital": "notificacion_electronica",
    "emplazamiento": "emplazamiento",
    "emplazamientos": "emplazamiento",
    # Escritos y recursos
    "alegato": "alegato",
    "alegatos": "alegato",
    "alegacion": "alegato",
    "recurso": "recurso",
    "recursos": "recurso",
    "interposicionrecurso": "recurso",
    "interposicionderecurso": "recurso",
    "concesionrecurso": "concesion_recurso",
    "concesiondelrecurso": "concesion_recurso",
    "recursodeapelacion": "apelacion",
    "apelacion": "apelacion",
    "recursodecasacion": "casacion",
    "casacion": "casacion",
    "queja": "queja",
    "quejas": "queja",
    "revision": "revision",
    "nulidad": "nulidad",
    "nulidades": "nulidad",
    "reposicion": "reposicion",
    "desistimiento": "desistimiento",
    # Pruebas
    "prueba": "prueba",
    "pruebas": "prueba",
    "solicitudprueba": "solicitud_prueba",
    "solicituddeprueba": "solicitud_prueba",
    "solicituddepruebas": "solicitud_prueba",
    "decretoprueba": "decreto_prueba",
    "decretodeprueba": "decreto_prueba",
    "practicaprueba": "practica_prueba",
    "practicadeprueba": "practica_prueba",
    "testimonio": "testimonio",
    "testimonios": "testimonio",
    "interrogatorio": "interrogatorio",
    "interrogatorios": "interrogatorio",
    "dictamen": "dictamen",
    "dictamenpericial": "dictamen",
    "dictamenexperto": "dictamen",
    "inspeccionjudicial": "inspeccion_judicial",
    "inspeccion": "inspeccion_judicial",
    "incorporaciondocumental": "incorporacion_documental",
    "memorial": "memorial",
    "memoriales": "memorial",
    # Vías constitucionales e incidentes
    "tutela": "tutela",
    "acciondetutela": "tutela",
    "incidente": "incidente",
    "incidentes": "incidente",
    "medidacautelar": "medida_cautelar",
    "medidascautelares": "medida_cautelar",
    "cautelar": "medida_cautelar",
    "cautelares": "medida_cautelar",
    # Ejecución y misceláneos
    "ejecucion": "ejecucion",
    "ejecuciones": "ejecucion",
    "otro": "otro",
    "otros": "otro",
    "desconocido": "otro",
}


# Etiquetas legibles para UI/reportes.
_SUBTYPE_LABELS: Final[dict[str, dict[str, str]]] = {
    "es": {
        "demanda": "Demanda",
        "contestacion": "Contestación de demanda",
        "excepcion": "Excepción",
        "reconvencion": "Reconvención",
        "auto": "Auto",
        "auto_admisorio": "Auto admisorio",
        "auto_de_pruebas": "Auto de pruebas",
        "auto_inhibitorio": "Auto inhibitorio",
        "sentencia": "Sentencia",
        "notificacion": "Notificación",
        "notificacion_personal": "Notificación personal",
        "notificacion_por_estado": "Notificación por estado",
        "notificacion_electronica": "Notificación electrónica",
        "emplazamiento": "Emplazamiento",
        "alegato": "Alegato",
        "recurso": "Recurso",
        "apelacion": "Apelación",
        "casacion": "Casación",
        "queja": "Queja",
        "revision": "Revisión",
        "nulidad": "Nulidad",
        "reposicion": "Reposición",
        "desistimiento": "Desistimiento",
        "prueba": "Prueba",
        "solicitud_prueba": "Solicitud de prueba",
        "decreto_prueba": "Decreto de prueba",
        "practica_prueba": "Práctica de prueba",
        "testimonio": "Testimonio",
        "interrogatorio": "Interrogatorio",
        "dictamen": "Dictamen",
        "inspeccion_judicial": "Inspección judicial",
        "incorporacion_documental": "Incorporación documental",
        "memorial": "Memorial",
        "tutela": "Tutela",
        "incidente": "Incidente",
        "medida_cautelar": "Medida cautelar",
        "ejecucion": "Ejecución",
        "concesion_recurso": "Concesión de recurso",
        "otro": "Otro",
    },
    "en": {
        "demanda": "Complaint",
        "contestacion": "Answer to complaint",
        "excepcion": "Exception",
        "reconvencion": "Counterclaim",
        "auto": "Order",
        "auto_admisorio": "Admissory order",
        "auto_de_pruebas": "Evidence order",
        "auto_inhibitorio": "Inhibitory order",
        "sentencia": "Judgment",
        "notificacion": "Notification",
        "notificacion_personal": "Personal notification",
        "notificacion_por_estado": "Notification by publication",
        "notificacion_electronica": "Electronic notification",
        "emplazamiento": "Summons",
        "alegato": "Argument",
        "recurso": "Remedy",
        "apelacion": "Appeal",
        "casacion": "Cassation",
        "queja": "Grievance",
        "revision": "Review",
        "nulidad": "Nullity",
        "reposicion": "Reinstatement",
        "desistimiento": "Withdrawal",
        "prueba": "Evidence",
        "solicitud_prueba": "Evidence request",
        "decreto_prueba": "Evidence decree",
        "practica_prueba": "Evidence practice",
        "testimonio": "Testimony",
        "interrogatorio": "Interrogatory",
        "dictamen": "Expert opinion",
        "inspeccion_judicial": "Judicial inspection",
        "incorporacion_documental": "Document incorporation",
        "memorial": "Petition",
        "tutela": "Tutela",
        "incidente": "Incident",
        "medida_cautelar": "Preliminary injunction",
        "ejecucion": "Enforcement",
        "concesion_recurso": "Grant of remedy",
        "otro": "Other",
    },
}


def normalize_subtype(raw: str | None) -> str | None:
    """Devuelve el subtipo canónico o ``None`` si no se reconoce.

    Acepta cadenas con acentos, mayúsculas, guiones bajos/medios o espacios.
    """
    if raw is None:
        return None
    key = _normalize_key(raw)
    if not key:
        return None
    if key in _SUBTYPE_SYNONYMS:
        return _SUBTYPE_SYNONYMS[key]
    # Si la entrada ya es un canónico (p. ej. sin acentos), devolverla tal cual.
    if key in PROCEDURAL_SUBTYPES:
        return key
    return None


def is_valid_subtype(subtype: str | None) -> bool:
    """Indica si ``subtype`` es un valor canónico permitido (o ``None``)."""
    if subtype is None:
        return True
    return subtype in PROCEDURAL_SUBTYPES


def subtype_label(subtype: str, locale: str = "es") -> str:
    """Etiqueta legible para un subtipo canónico.

    Si el subtipo no es canónico o no tiene etiqueta, devuelve la cadena original.
    """
    labels = _SUBTYPE_LABELS.get(locale, _SUBTYPE_LABELS["es"])
    return labels.get(subtype, subtype)


def list_subtypes() -> list[str]:
    """Lista ordenada de subtipos canónicos."""
    return sorted(PROCEDURAL_SUBTYPES)
