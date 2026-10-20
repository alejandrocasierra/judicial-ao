"""Extracción de PARTES (demandante/demandado/ejecutante/…) desde los encabezados de los autos.

Heurística ESTRICTA sobre el OCR: reconoce el cargo de parte (demandante/demandado/ejecutante/…)
y toma el TRAMO INICIAL en mayúsculas a su lado como nombre; clasifica persona/ organización y
deduplica por nombre normalizado. Pensada para REVISIÓN humana (panel) antes de confirmar.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import rows

# Palabra de rol → rol canónico de `parties.role` (solo partes: demandante/demandado).
_ROLE_MAP: dict[str, str] = {
    "demandante": "claimant", "demandantes": "claimant", "demandada": "claimant", "demandadas": "claimant",
    "ejecutante": "claimant", "ejecutantes": "claimant", "denunciante": "claimant", "denunciantes": "claimant",
    "accionante": "claimant", "accionantes": "claimant", "actor": "claimant", "actores": "claimant",
    "demandado": "defendant", "demandados": "defendant", "denunciado": "defendant", "denunciados": "defendant",
    "ejecutado": "defendant", "ejecutados": "defendant",
}

_ROLE_WORDS = ("demandantes?|demandadas?|ejecutantes?|ejecutados?|denunciantes?|denunciados?|"
               "accionantes?|actores?")
# "DEMANDANTE: Nombre" / "DEMANDADO - Nombre"
_RE_AFTER = re.compile(rf"(?i:\b({_ROLE_WORDS})\b)\s*[:.\-–]\s*([^\n;|]{{3,90}})")
# "Nombre DEMANDADO" / "Nombre, DEMANDANTE"  (el nombre va antes del cargo)
_RE_BEFORE = re.compile(rf"(?im)^\s*([^\n;:|]{{3,80}}?)\s*,?\s*(?i:\b({_ROLE_WORDS})\b)\s*[:.\-–]?\s*$")

_ORG_RE = re.compile(r"(?i)\b(s\.?\s?a\.?\s?s?\.?|s\.?\s?a\.?|ltda\.?|limitada|e\.?\s?u\.?|"
                     r"corp(oraci[oó]n)?|sociedad|fondo|banco|aseguradora|consorcio|uni[oó]n temporal|inversiones|"
                     r"comercializadora|constructora|servicios)\b")

# Palabras que NO forman parte de un nombre de parte.
_STOP = {
    "EL", "LA", "LOS", "LAS", "DE", "DEL", "Y", "EN", "POR", "CON", "SEÑOR", "SEÑORA", "SR", "SRA",
    "PARTE", "PARTES", "DEMANDANTE", "DEMANDADO", "DEMANDADOS", "DEMANDADAS", "EJECUTANTE", "EJECUTADO",
    "ACTOR", "ACCIONANTE", "JUDICIAL", "CALIDAD", "COMO", "OBRA", "ACTUANDO", "FIRMA", "CORRESPONDIENTE",
    "EXTREMO", "PROCESO", "EXPEDIENTE", "RADICADO", "JUZGADO", "DESPACHO", "CIRCUITO", "MUNICIPAL",
    "AUTOS", "AUTO", "REF", "REFERENCIA", "DEMANDA", "PROCESAL", "DOCTOR", "DOCTORA", "ABOGADO",
    "ABOGADA", "APODERADO", "APODERADA", "REPRESENTANTE", "REPRESENTANTES", "MEDIANTE", "DENTRO",
    "CONTRA", "FAVOR", "SEGUNDO", "TERCERO", "PRIMERO", "RESPECTO", "SOBRE", "SEÑALA", "INFORMA",
    "NO", "SI", "PRUEBA", "PRINCIPAL", "IDENTIFICADO", "IDENTIFICADA", "IDENTIFICACION",
    "DOMICILIADO", "DOMICILIADA", "PORTADOR", "PORTADORA", "MAYOR", "MENOR", "EDAD",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents((name or "").upper()))


def entity_type(name: str) -> str:
    return "organization" if _ORG_RE.search(name or "") else "person"


def _is_name_token(tok: str) -> bool:
    t = tok.strip(".,;:()[]")
    if len(t) < 2:
        return False
    if t.upper() in _STOP:
        return False
    return bool(re.match(r"^[A-ZÁÉÍÓÚÜÑ0-9]", t))


def _clean(raw: str) -> str | None:
    """Toma el TRAMO INICIAL de tokens tipo-nombre (mayúscula inicial) y descarta el resto."""
    s = re.sub(r"^[\s,;.:\-–·]+", "", (raw or "").strip())
    keep: list[str] = []
    for tok in s.split():
        if _is_name_token(tok):
            keep.append(tok.strip(".,;:()[]"))
            if len(keep) == 5:
                break
        else:
            break
    name = re.sub(r"\s+", " ", " ".join(keep)).strip(" ,;.:-–")
    # 2-4 tokens tipo-nombre, con al menos uno "de verdad" (letra, no solo números).
    if not (2 <= len(keep) <= 4) or len(name) < 5:
        return None
    if not any(len(t) >= 3 and t[:1].isalpha() for t in keep):
        return None
    return name


def extract(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    """Devuelve candidatos de partes (nombre, rol, tipo, menciones y cita). No persiste nada."""
    pages = rows(conn, """
        SELECT p.document_id, d.filename, p.page_number, p.folio, p.text
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c
        ORDER BY d.filename, p.page_number
    """, c=case_id)
    cands: dict[str, dict[str, Any]] = {}

    def add(raw: str, role_word: str, pg: dict[str, Any]) -> None:
        name = _clean(raw)
        if not name:
            return
        key = normalize_name(name)
        if not key:
            return
        role = _ROLE_MAP.get(role_word.lower().strip("."), "third_party")
        cur = cands.get(key)
        if cur is None:
            cands[key] = {"name": name, "normalized_name": key, "role": role,
                          "entity_type": entity_type(name), "mentions": 1,
                          "document_id": str(pg["document_id"]), "filename": pg["filename"],
                          "page_number": pg["page_number"], "folio": pg["folio"], "aliases": []}
        else:
            cur["mentions"] += 1

    for pg in pages:
        txt = pg["text"] or ""
        for m in _RE_AFTER.finditer(txt):
            add(m.group(2), m.group(1), pg)
        for m in _RE_BEFORE.finditer(txt):
            add(m.group(1), m.group(2), pg)
    return sorted(cands.values(), key=lambda c: -c["mentions"])


def create_parties(conn: Connection, org_id: str, case_id: str, actor_id: str | None,
                   items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inserta las partes indicadas, evitando duplicados por nombre normalizado. Devuelve las nuevas."""
    existing = {r["normalized_name"] for r in rows(
        conn, "SELECT normalized_name FROM parties WHERE case_id = :c", c=case_id)}
    created: list[dict[str, Any]] = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        if not key or key in existing:
            continue
        role = (it.get("role") or "").strip() or "third_party"
        etype = it.get("entity_type") if it.get("entity_type") in ("person", "organization") else entity_type(name)
        aliases = [a for a in (it.get("aliases") or []) if a]
        row = conn.execute(text("""INSERT INTO parties (organization_id, case_id, name, normalized_name, role,
                                       entity_type, aliases)
            VALUES (:o, :c, :n, :nn, :r, :e, :al) RETURNING id, name, role, entity_type, aliases"""),
            {"o": org_id, "c": case_id, "n": name, "nn": key, "r": role, "e": etype, "al": aliases}).mappings().first()
        existing.add(key)
        created.append(dict(row))
    return created


# ---------------------------------------------------------------- automático


# Encabezados de rol (normalizados) -> código de rol de `parties`.
_ROLE_HEADERS: dict[str, str] = {
    "demandante": "claimant", "demandantes": "claimant",
    "ejecutante": "claimant", "ejecutantes": "claimant",
    "accionante": "claimant", "accionantes": "claimant",
    "denunciante": "claimant", "denunciantes": "claimant",
    "demandado": "defendant", "demandados": "defendant",
    "ejecutado": "defendant", "ejecutados": "defendant",
    "denunciado": "defendant", "denunciados": "defendant",
    "apoderado": "attorney", "apoderados": "attorney",
    "abogado": "attorney", "abogada": "attorney",
    "representante": "representative", "representantes": "representative",
    "tercero": "third_party", "terceros": "third_party",
    "testigo": "witness", "testigos": "witness",
    "perito": "expert", "peritos": "expert",
    "juez": "judge", "jueza": "judge",
    "magistrado": "magistrado", "magistrada": "magistrado",
    "magistrados": "magistrado", "magistradas": "magistrado",
    "magistrado ponente": "magistrado", "magistrada ponente": "magistrado",
    "ponente": "magistrado",
}

# Apoderado + lado de la parte: "APODERADO DE LA PARTE DEMANDANTE", "APODERADA DEL DEMANDADO".
_ATTORNEY_RE = re.compile(r"^apoderad[oa]s?\b")
_SIDE_CLAIMANT_RE = re.compile(
    r"\b(demandantes?|actora|actor|ejecutantes?|accionantes?|denunciantes?)\b")
_SIDE_DEFENDANT_RE = re.compile(r"\b(demandad[oa]s?|ejecutad[oa]s?|denunciad[oa]s?)\b")

# Líneas que NO son nombres (encabezados/ruido de OCR). Normalizadas sin acentos/espacios.
_NAME_NOISE = {
    "CONTENIDODERADICACION", "CONTENIDO", "ACTUACIONES", "ACTUACIONESDELPROCESO", "UBICACION",
    "UBICACIONDELEXPEDIENTE", "DESPACHO", "FECHA", "ANOTACION", "SUJETOSPROCESALES", "SUJETOS",
    "PROCESALES", "PROCESO", "RADICADO", "CLASE", "DECISION", "PROVIDENCIA", "RESUELVE",
    "CONSIDERANDO", "HECHOS", "FUNDAMENTOS", "PRETENSIONES", "NOTIFICACION", "SECRETARIA",
    "TERMINOS", "INICIO", "REPUBLICA", "RAMAJUDICIAL", "RAMA", "JUDICIAL", "PODERPUBLICO",
    "PODER", "PUBLICO", "JUSTICIA", "CONSULTA", "DETALLE", "REGISTRO", "AVISO",
    "AVISODECONFIDENCIALIDAD", "FIRMADO", "FIRMADOPOR", "DEMANDANTES", "DEMANDADOS",
    "CLASEDEPROCESO", "TIPOPERSONA", "TIPOSUJETO", "REGRESAR", "PONENTE",
}

# Palabras que delatan un despacho/cargo/etiqueta (no un nombre de parte).
_BAD_WORDS = {
    "JUZGADO", "SALA", "TRIBUNAL", "CORTE", "DESPACHO", "MUNICIPAL", "CIRCUITO", "CIVIL",
    "FAMILIA", "LABORAL", "PROMISCUO", "PENAL", "CONSEJO", "SECCIONAL", "SUPERIOR",
    "DEFENSOR", "DEFENSORA", "DEFENSORIA", "PRIVADO", "PRIVADA", "LEGAL", "SUPLENTE",
    "NIT", "CC", "CEDULA", "CIUDA", "CIUDADANIA", "PASAPORTE", "SEÑOR", "SEÑORA",
    "EMBAJADA", "CONSULADO", "NOTARIA", "REGISTRADURIA", "SIM", "NUMERO", "NOMBRE",
    "APELLIDO", "APELLIDOS", "DIRECCION", "TELEFONO", "CORREO", "ELECTRONICO",
}


def _looks_like_name(raw: str) -> str | None:
    """Nombre de persona/sociedad: 2-5 tokens, cada uno con inicial mayúscula, sin dígitos
    ni ':' (evita capturar párrafos o encabezados). Alta precisión para uso automático."""
    s = re.sub(r"^[\s\-•*·>»]+", "", raw or "").strip()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) < 5 or len(s) > 70:
        return None
    if any(ch.isdigit() for ch in s) or ":" in s or "@" in s:
        return None
    toks = s.split()
    # Quita conectores/colectivos finales: "JUAN CARLOS GARZON Y", "… Y OTROS".
    while toks and strip_accents(toks[-1]).upper() in {"Y", "E", "A", "O", "OTROS", "OTRAS"}:
        toks.pop()
    if not (2 <= len(toks) <= 5):
        return None
    if not all(re.match(r"^[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ.\-]*$", t) for t in toks):
        return None
    # Quita punto final de "Nombre Apellido." salvo siglas ("S.A.S.").
    if toks[-1].endswith(".") and not re.match(r"^(?:[A-Za-z]\.)+$", toks[-1]):
        toks[-1] = toks[-1].rstrip(".")
    norm_words = {re.sub(r"[^A-Z]", "", strip_accents(t).upper()) for t in toks}
    if norm_words & _BAD_WORDS:  # despachos/cargos/legal: no son nombres
        return None
    joined = re.sub(r"[^A-Z]", "", strip_accents(s).upper())
    if joined in _NAME_NOISE or not any(len(t) >= 3 for t in toks):
        return None
    return " ".join(toks)


def _role_from_header(line: str) -> str | None:
    t = strip_accents(line).lower().strip()
    t = re.sub(r"[().:;\-]+$", "", t).strip()
    t = re.sub(r"\(s\)$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    exact = _ROLE_HEADERS.get(t)
    if exact:
        return exact
    # "Apoderado (de la parte) DEMANDANTE / DEMANDADA" -> rol con lado.
    if _ATTORNEY_RE.match(t):
        if _SIDE_DEFENDANT_RE.search(t):
            return "attorney_defendant"
        if _SIDE_CLAIMANT_RE.search(t):
            return "attorney_claimant"
    return None


def extract_structured(conn: Connection, case_id: str) -> list[dict[str, Any]]:
    """Partes de ALTA PRECISIÓN desde los encabezados oficiales del OCR.

    Reconoce bloques tipo "Demandante(s) \\n JOSE RUEDA AVELLANEDA", "Demandado(s) \\n - NOMBRE",
    "Ponente \\n NOMBRE", "Magistrado Ponente" y "ROL: NOMBRE". Pensado para crear partes
    automáticamente sin revisión (por eso es estricto)."""
    pages = rows(conn, """
        SELECT d.id AS document_id, d.filename, p.page_number, p.folio, p.text
        FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c ORDER BY d.filename, p.page_number
    """, c=case_id)
    found: dict[str, dict[str, Any]] = {}
    for pg in pages:
        current_role: str | None = None
        collected = 0
        for raw in (pg["text"] or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            inline = None
            if ":" in line:
                left, _, right = line.partition(":")
                r = _role_from_header(left)
                if r:
                    inline = (r, right)
            if inline:
                role, right = inline
                nm = _looks_like_name(right)
                if nm:
                    _add_candidate(found, nm, role, pg)
                current_role, collected = None, 0
                continue
            header_role = _role_from_header(line)
            if header_role:
                current_role, collected = header_role, 0
                continue
            if current_role is not None:
                nm = _looks_like_name(line)
                if nm:
                    _add_candidate(found, nm, current_role, pg)
                    collected += 1
                    if collected >= 3:
                        current_role = None
                else:
                    current_role = None
    return sorted(found.values(), key=lambda c: (-c["mentions"], c["name"]))


def _add_candidate(found: dict[str, dict[str, Any]], name: str, role: str, pg: dict[str, Any]) -> None:
    key = normalize_name(name)
    if not key:
        return
    if key in found:
        found[key]["mentions"] += 1
        if found[key]["role"] == "third_party" and role != "third_party":
            found[key]["role"] = role
        return
    found[key] = {"name": name, "normalized_name": key, "role": role,
                  "entity_type": entity_type(name), "mentions": 1,
                  "document_id": str(pg["document_id"]), "filename": pg["filename"],
                  "page_number": pg["page_number"], "folio": pg["folio"], "aliases": []}


def _lev(a: str, b: str) -> int:
    """Distancia de Levenshtein (para tolerar typos leves en nombres)."""
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def auto_upsert(conn: Connection, org_id: str, case_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Crea las partes que falten (dedup por nombre normalizado) y omite las existentes.

    Deduplicación difusa: omite si el nombre es submconjunto de tokens de una parte
    existente ("JUAN CARLOS GARZÓN" ⊂ "Juan Carlos Garzón Gutiérrez"), si cada token
    tiene un par a distancia ≤1 ("GARCON" ≈ "GARZON"), o si el nombre es muy similar.
    Si una parte existe con rol genérico (`third_party`) y ahora se detecta uno
    específico, actualiza el rol."""
    import difflib

    def tokens(name: str) -> set[str]:
        return set(re.findall(r"[A-Z0-9]+", strip_accents(name or "").upper()))

    existing = rows(conn, "SELECT id, name, normalized_name, role FROM parties WHERE case_id = :c", c=case_id)
    by_key = {r["normalized_name"]: r for r in existing}

    def find_match(name: str, key: str) -> dict | None:
        if key in by_key:
            return by_key[key]
        kt = tokens(name)
        for r in existing:
            et = tokens(r["name"])
            if kt and kt <= et:
                return r
            # typos leves: cada token del candidato tiene un par cercano (tokens >=4 letras).
            if kt and all(any(len(ct) >= 4 and len(t) >= 4 and _lev(ct, t) <= 1 for t in et) for ct in kt):
                return r
            if abs(len(key) - len(r["normalized_name"])) <= 4 and \
                    difflib.SequenceMatcher(None, key, r["normalized_name"]).ratio() >= 0.92:
                return r
        return None

    created: list[dict[str, Any]] = []
    skipped = 0
    for it in items:
        name = (it.get("name") or "").strip()
        key = normalize_name(name)
        if not key:
            continue
        new_role = (it.get("role") or "").strip() or "third_party"
        match = find_match(name, key)
        if match:
            if new_role != "third_party" and (match.get("role") in (None, "", "third_party")):
                conn.execute(text("UPDATE parties SET role = :r WHERE id = :i"),
                             {"r": new_role, "i": str(match["id"])})
                match["role"] = new_role
            skipped += 1
            continue
        etype = it.get("entity_type") if it.get("entity_type") in ("person", "organization") else entity_type(name)
        row = conn.execute(text("""INSERT INTO parties (organization_id, case_id, name, normalized_name, role,
                                       entity_type, aliases)
            VALUES (:o, :c, :n, :nn, :r, :e, :al) RETURNING id, name, role, entity_type, aliases"""),
            {"o": org_id, "c": case_id, "n": name, "nn": key, "r": new_role, "e": etype,
             "al": [a for a in (it.get("aliases") or []) if a]}).mappings().first()
        new_row = dict(row)
        new_row["normalized_name"] = key
        existing.append(new_row)
        by_key[key] = new_row
        created.append(new_row)
    return {"created": created, "skipped": skipped}


def auto_extract_and_upsert(conn: Connection, org_id: str, case_id: str) -> dict[str, Any]:
    """Extrae partes del OCR (encabezados) y del ASR (hablantes con nombre/rol) y crea las que falten."""
    from app.services import party_roles

    party_roles.ensure_defaults(conn, org_id, case_id)
    items = extract_structured(conn, case_id)
    # Hablantes del ASR ya identificados (nombre + rol).
    for s in rows(conn, """SELECT display_name, speaker_role FROM speakers
                           WHERE case_id = :c AND coalesce(display_name,'') <> ''""", c=case_id):
        items.append({"name": s["display_name"], "role": s["speaker_role"] or "third_party",
                      "entity_type": entity_type(s["display_name"])})
    res = auto_upsert(conn, org_id, case_id, items)
    # Automático: asigna el lado (demandante/demandada) de cada apoderado buscándolo en el OCR.
    res["attorney_sides"] = infer_attorney_sides(conn, case_id)
    return res


# --- Inferencia automática del LADO del apoderado (demandante / demandada) ---
# Solo señales FUERTES sobre el texto normalizado (sin acentos, mayúsculas):
#  A) "APODERAD[OA] ... <apellido>" con la palabra del lado en la MISMA frase.
#  B) bloque "APODERADO: <apellido>" y la última etiqueta previa DEMANDANTE:/DEMANDADO:.
_ATT = re.compile(r"APODERAD[OA]")
_SIDE_CLAIM = re.compile(r"\b(DEMANDANTE|ACTORA|EJECUTANTE|ACCIONANTE)")
_SIDE_DEF = re.compile(r"\b(DEMANDAD[OA]|EJECUTAD[OA])")
_APODERADO_BLOCK = re.compile(r"APODERAD[OA]\s*:\s*([^\n]{0,90})")


def _surname_tokens(name: str) -> list[str]:
    """Los 1-2 apellidos (últimos tokens) para buscar al apoderado en el texto."""
    toks = [t for t in re.findall(r"[A-Z0-9]+", strip_accents(name or "").upper()) if len(t) >= 3]
    return toks[-2:] if len(toks) >= 2 else toks


def _vote_window(window: str, surnames: list[str], votes: dict[str, int]) -> None:
    """Vota el lado para cada 'APODERADO … <apellido>' cuyo lado aparece en la misma frase."""
    for m in _ATT.finditer(window):
        seg = window[m.end(): m.end() + 80]
        if not any(sn in seg for sn in surnames):
            continue
        c = _SIDE_CLAIM.search(seg)
        d = _SIDE_DEF.search(seg)
        ci = c.start() if c else 10 ** 9
        di = d.start() if d else 10 ** 9
        if ci < di:
            votes["claimant"] += 2
        elif di < ci:
            votes["defendant"] += 2


def infer_attorney_sides(conn: Connection, case_id: str) -> dict[str, Any]:
    """A partir del OCR/ASR, asigna `attorney_claimant`/`attorney_defendant` a los apoderados
    genéricos cuando el texto lo evidencia con claridad. Conservador: si el apoderado aparece
    en AMBOS extremos (histórico largo) o no hay señal, se deja el rol genérico."""
    attorneys = rows(conn, "SELECT id, name FROM parties "
                           "WHERE case_id = :c AND role = 'attorney'", c=case_id)
    if not attorneys:
        return {"checked": 0, "assigned": 0}

    texts: list[str] = [strip_accents(r["text"] or "").upper() for r in rows(conn, """
        SELECT p.text FROM document_pages p JOIN documents d ON d.id = p.document_id
        WHERE d.case_id = :c""", c=case_id)]
    texts += [strip_accents(r["text"] or "").upper() for r in rows(conn, """
        SELECT s.text FROM transcript_segments s JOIN media m ON m.id = s.media_id
        WHERE m.case_id = :c""", c=case_id)]

    assigned = 0
    for a in attorneys:
        surnames = _surname_tokens(a["name"])
        if not surnames:
            continue
        votes = {"claimant": 0, "defendant": 0}
        for t in texts:
            for sn in surnames:
                start = 0
                while True:
                    i = t.find(sn, start)
                    if i < 0:
                        break
                    _vote_window(t[max(0, i - 280): i + 340], surnames, votes)
                    start = i + 1
            for m in _APODERADO_BLOCK.finditer(t):
                if any(sn in m.group(1) for sn in surnames):
                    before = t[:m.start()]
                    pc, pd = before.rfind("DEMANDANTE:"), before.rfind("DEMANDADO:")
                    if pc > pd and pc >= 0:
                        votes["claimant"] += 2
                    elif pd > pc and pd >= 0:
                        votes["defendant"] += 2
        # Asigna por mayoría clara; si hay empate o señal débil, deja el rol genérico.
        if votes["claimant"] >= 2 and votes["claimant"] > votes["defendant"]:
            role = "attorney_claimant"
        elif votes["defendant"] >= 2 and votes["defendant"] > votes["claimant"]:
            role = "attorney_defendant"
        else:
            continue
        conn.execute(text("UPDATE parties SET role = :r WHERE id = :i AND case_id = :c"),
                     {"r": role, "i": str(a["id"]), "c": case_id})
        assigned += 1
    return {"checked": len(attorneys), "assigned": assigned}
