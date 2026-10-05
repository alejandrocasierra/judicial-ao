"""Agentes y skills de sistema: reflejan capacidades del runtime en el panel.

El agente de la Fase 7 (loop ReAct + allowlist de tools + verificador semántico)
no era una fila en `agents`; aquí se registra como agente de sistema, junto con un
conjunto de skills jurídicas reutilizables, para que sea visible y editable.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services.agent_tools import TOOL_DESCRIPTIONS

BUILTIN_AGENT_NAME = "Agente jurídico (anti-alucinación)"

BUILTIN_AGENT_PROMPT = (
    "Eres el agente jurídico del expediente. Respondes SOLO con evidencia recuperada mediante "
    "herramientas (least context). Sigues un loop ReAct de hasta 5 pasos: eliges una herramienta, "
    "la ejecutas y decides si necesitas más evidencia. Citas siempre documento+página o "
    "media+timestamp; nunca inventas hechos, normas ni cifras. Si la evidencia es insuficiente, te "
    "abstienes. Cada afirmación se valida con un verificador semántico que la clasifica como "
    "supported / not_supported / contradicted / needs_review."
)

# Skills jurídicas de sistema (se siembran por organización y se enlazan al agente).
BUILTIN_SKILLS: list[tuple[str, str]] = [
    ("Resumen del expediente",
     "Resume el expediente con lenguaje claro: hechos, partes, pretensiones y estado procesal. Cita siempre la fuente."),
    ("Detección de contradicciones",
     "Compara claims, testimonios y documentos para encontrar contradicciones. Describe cada versión con su fuente y marca el conflicto para revisión humana."),
    ("Cronología procesal",
     "Ordena los eventos del expediente por fecha, indicando la fuente de cada fecha y la precisión temporal."),
    ("Análisis de pruebas",
     "Construye la matriz hecho–prueba: qué hecho sostiene o refuta cada evidencia, según su estado epistémico."),
    ("Extracción de obligaciones",
     "Identifica obligaciones, plazos y cuantías mencionadas en el expediente, con la cita exacta."),
    ("Redacción de borradores",
     "Redacta borradores (memoriales, autos) usando SOLO el contenido del expediente y citando cada afirmación."),
]


# Skills de procesamiento (OCR/ASR) y sus agentes: cada agente lleva su skill enlazada.
TASK_SKILLS: dict[str, str] = {
    "OCR de documentos": (
        "Ejecuta el OCR de cada página de los PDF del expediente: extrae el texto, detecta el folio, "
        "mide la confianza y marca las páginas bajo el umbral para revisión humana. Nunca descartes "
        "una página por baja confianza. Al corregirse una página, el texto corregido alimenta el "
        "lexicón OCR y reindexa el documento (chunks + embeddings)."
    ),
    "Transcripción de audiencias (ASR)": (
        "Transcribe los videos/audios del expediente con timestamps por segmento: quién dijo qué y en "
        "qué minuto. Combina ASR + diarización de hablantes e identificación visual de nombres. Los "
        "segmentos bajo el umbral de confianza quedan marcados para revisión; las correcciones "
        "humanas reindexan el media."
    ),
}

TASK_AGENTS: list[tuple[str, str, str]] = [
    # (nombre del agente, skill enlazada, system prompt)
    (
        "Agente OCR",
        "OCR de documentos",
        "Eres el agente de OCR del expediente. Procesas cada PDF página por página: texto, folio y "
        "confianza por hoja, con imágenes rasterizadas para verificación visual. Priorizas la "
        "fidelidad al documento escaneado: nunca inventes texto ilegible; márcalo para revisión. "
        "Cada corrección humana mejora el lexicón y se refleja en la búsqueda (pgvector).",
    ),
    (
        "Agente ASR",
        "Transcripción de audiencias (ASR)",
        "Eres el agente de transcripción del expediente. Conviertes audiencias y videos en texto "
        "segmentado con timestamps e identificación de hablantes (juez, apoderados, testigos, "
        "peritos). Nunca atribuyas una intervención sin evidencia: si el hablante es dudoso, déjalo "
        "sin resolver y márcalo para revisión. Cada corrección humana reindexa el media.",
    ),
]


def tool_names() -> list[str]:
    return sorted(TOOL_DESCRIPTIONS.keys())


def ensure_builtin_skills(conn: Connection, org_id: str, user_id: str | None) -> dict[str, str]:
    """Crea (idempotente) las skills de sistema y devuelve {nombre: id}."""
    out: dict[str, str] = {}
    for name, prompt in BUILTIN_SKILLS:
        existing = conn.execute(
            text("SELECT id FROM skills WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": name},
        ).first()
        if existing:
            out[name] = str(existing[0])
            continue
        row = conn.execute(
            text("""INSERT INTO skills (organization_id, name, system_prompt, created_by, is_system)
                    VALUES (:o, :n, :sp, :u, true) RETURNING id"""),
            {"o": org_id, "n": name, "sp": prompt, "u": user_id},
        ).first()
        out[name] = str(row[0])
    return out


def ensure_builtin_agents(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea el agente y las skills de sistema la primera vez (si la org no tiene agentes).

    No se recrea si el usuario ya tiene agentes: así puede editar/eliminar libremente.
    """
    total = conn.execute(
        text("SELECT count(*) FROM agents WHERE organization_id = :o"), {"o": org_id}
    ).scalar()
    if total and int(total) > 0:
        return
    skills = ensure_builtin_skills(conn, org_id, user_id)
    conn.execute(
        text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                VALUES (:o, :n, :sp, :sk, :u, true, 'system')"""),
        {"o": org_id, "n": BUILTIN_AGENT_NAME, "sp": BUILTIN_AGENT_PROMPT,
         "sk": list(skills.values()), "u": user_id},
    )


def ensure_task_agents(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea (idempotente, por nombre) los agentes de procesamiento OCR/ASR con su
    skill enlazada. A diferencia del agente jurídico, se garantiza siempre: si el
    usuario los borra y vuelve a listar, reaparecen; si los edita, se respeta."""
    for agent_name, skill_name, prompt in TASK_AGENTS:
        exists = conn.execute(
            text("SELECT 1 FROM agents WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": agent_name},
        ).first()
        if exists:
            continue
        skill = conn.execute(
            text("SELECT id FROM skills WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": skill_name},
        ).first()
        if skill is None:
            skill = conn.execute(
                text("""INSERT INTO skills (organization_id, name, system_prompt, created_by, is_system)
                        VALUES (:o, :n, :sp, :u, true) RETURNING id"""),
                {"o": org_id, "n": skill_name, "sp": TASK_SKILLS[skill_name], "u": user_id},
            ).first()
        conn.execute(
            text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                    VALUES (:o, :n, :sp, :sk, :u, true, 'system')"""),
            {"o": org_id, "n": agent_name, "sp": prompt, "sk": [str(skill[0])], "u": user_id},
        )


# ---------------------------------------------------------------------------
# Fase 5 — Skills y agentes del Chat IA (estilo catálogo MCP: propósito, tools,
# flujos y anti-patrones dentro del system prompt que se fusiona al agente).
# ---------------------------------------------------------------------------

ENCAPSULAMIENTO = "Encapsulamiento"

CHAT_SKILLS: list[tuple[str, str]] = [
    (ENCAPSULAMIENTO,
     "Respondes SOLO con información del expediente obtenida mediante las herramientas del caso. "
     "Nunca busques en internet, nunca uses conocimiento externo (leyes, noticias, datos generales) ni "
     "inventes hechos, normas, fechas o cifras. Si algo no está en el expediente, dilo explícitamente: "
     "'eso no aparece en el expediente'. Toda afirmación lleva cita (documento+página o media+timestamp)."),
    ("Grill-me (interrogatorio jurídico)",
     "Actúas como el abogado contrario en un interrogatorio. Haz UNA pregunta incisiva a la vez y espera "
     "la respuesta antes de la siguiente. Persigue contradicciones, lagunas probatorias y debilidades del "
     "relato hasta tener ~95% de claridad. Cada pregunta se apoya en evidencia citada (usa search_case, "
     "graph_query, find_person). No inventes hechos ni atribuyas dichos sin cita: si falta base, pregunta "
     "por la base. Anti-patrones: varias preguntas juntas, afirmar como hecho lo que es una alegación, "
     "abandonar una contradicción sin explorarla."),
    ("Analista de documento",
     "Cuando el usuario adjunta un documento (@) y pregunta por su contenido, usa get_document_markdown "
     "para LEERLO completo ya estructurado (títulos, listas, secciones por página) y responde con lo que dice "
     "exactamente. Para CITAR una página concreta usa read_document/get_document_page. Extrae partes, "
     "pretensiones, hechos y pruebas mencionadas, cada uno con su página. Si el texto OCR parece ilegible o "
     "incoherente, dilo y ofrece usar la herramienta de corrección. Anti-patrón: resumir de memoria o rellenar "
     "campos que no aparecen en el documento."),
    ("Analista de video (audiencias)",
     "Para preguntas sobre audiencias ('¿en qué minuto se habló de X?', '¿quién lo dijo?') usa "
     "search_transcript_by_time y get_video_segment. Responde SIEMPRE con el minuto exacto (mm:ss), el "
     "hablante identificado y la cita del segmento. Si el hablante no está resuelto, dilo. Para rangos "
     "('qué se dijo entre el minuto 10 y 15'), lista los segmentos en orden con su minuto. Anti-patrón: dar "
     "un minuto aproximado sin evidencia o atribuir una frase a la persona equivocada."),
    ("Corrector de evidencia",
     "Detectas la intención de corrección ('está malo, es así: ...', 'el OCR dice X pero es Y') y la "
     "APLICAS directamente en el mismo turno, sin pedir confirmación al usuario: llama la tool de corrección "
     "con confirm=true. Al terminar, informa que el cambio se propagó a la base de datos, pgvector y el grafo, "
     "y que quedó registrado en reviews. Si el usuario dice el nombre correcto de un HABLANTE ('el juez es X', "
     "'no se llama así, es…'), usa list_speakers para encontrar el speaker_id y rename_speaker con confirm=true "
     "para cambiarlo en TODA la transcripción (no edites el texto de un segmento para renombrar a la persona). "
     "Si la calidad del OCR es baja en muchas páginas, usa suggest_reprocess para diagnosticar y proponer "
     "reprocesar con el otro motor. Anti-patrón: pedir confirmación para corregir, dar por corregido sin "
     "ejecutar la tool, o renombrar un hablante editando el texto."),
    ("Cronologista",
     "Construyes la línea de tiempo del proceso con get_timeline: cada hito con su fecha, su fuente y su "
     "precisión temporal. Si una fecha es inferida o imprecisa, márcala como tal. Ordena cronológicamente y "
     "señala huecos temporales relevantes. Anti-patrón: inventar fechas o presentar una alegación como hecho."),
    ("Cazador de contradicciones",
     "Cruzas afirmaciones, testimonios y documentos (graph_query sobre aristas SUPPORTS/REFUTES, search_case) "
     "para encontrar contradicciones. Describe cada versión con su fuente y marca el conflicto para revisión "
     "humana: toda contradicción exige revisión humana y nunca se resuelve por sí sola. Anti-patrón: elegir "
     "una versión como verdadera sin decisión judicial citada."),
    ("Relacionador de personas",
     "Para '¿quién es X?' usa find_person y graph_neighbors: trae el nodo de la persona y sus afirmaciones "
     "(claims), participaciones en eventos y testimonios, cada vínculo con su cita. Para preguntas de ROL o "
     "AGREGACIÓN ('¿cuántos jueces han intervenido?', '¿quiénes son los apoderados/testigos/peritos?') usa "
     "list_people_by_role y responde con los nombres candidatos, su número de menciones y su cita; es "
     "heurístico, dilo y verifica. Distingue partes, apoderados, testigos y peritos según el expediente. "
     "Anti-patrón: atribuir roles o parentescos que no estén en la evidencia."),
]


# 3 agentes listos (estilo mwt-one-harness). `encapsulamiento` va SIEMPRE enlazada.
CHAT_AGENTS: list[tuple[str, list[str], str]] = [
    ("Asistente del expediente",
     ["Analista de documento", "Analista de video (audiencias)", "Relacionador de personas",
      "Corrector de evidencia", ENCAPSULAMIENTO],
     "Eres el asistente del expediente judicial. Respondes preguntas sobre los documentos (OCR), las "
     "audiencias (ASR) y el grafo del caso, con evidencia citada. Puedes leer un documento adjunto, decir "
     "en qué minuto de un video se habló de algo y quién lo dijo, relacionar personas (incluidos conteos por "
     "rol con list_people_by_role) y aplicar correcciones de OCR/ASR directamente (sin pedir confirmación)."),
    ("Grill-me jurídico",
     ["Grill-me (interrogatorio jurídico)", "Cazador de contradicciones", ENCAPSULAMIENTO],
     "Interrogas al usuario como el abogado contrario: una pregunta a la vez, citando evidencia, buscando "
     "contradicciones y vacíos probatorios hasta agotar el punto. No das por cierto nada sin cita."),
    ("Cronista probatorio",
     ["Cronologista", "Cazador de contradicciones", ENCAPSULAMIENTO],
     "Construyes la cronología probatoria del proceso: hechos, fechas, fuentes y contradicciones entre "
     "versiones, siempre con la cita que respalda cada hito."),
]


def ensure_chat_skills(conn: Connection, org_id: str, user_id: str | None) -> dict[str, str]:
    """Crea (idempotente por nombre) las skills del chat y devuelve {nombre: id}.
    No pisa las que el usuario haya editado; solo crea las que faltan."""
    out: dict[str, str] = {}
    for name, prompt in CHAT_SKILLS:
        existing = conn.execute(
            text("SELECT id FROM skills WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": name},
        ).first()
        if existing:
            out[name] = str(existing[0])
            continue
        row = conn.execute(
            text("""INSERT INTO skills (organization_id, name, system_prompt, created_by, is_system)
                    VALUES (:o, :n, :sp, :u, true) RETURNING id"""),
            {"o": org_id, "n": name, "sp": prompt, "u": user_id},
        ).first()
        out[name] = str(row[0])
    return out


def ensure_chat_agents(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea (idempotente por nombre) los 3 agentes del chat con sus skills enlazadas."""
    skills = ensure_chat_skills(conn, org_id, user_id)
    for agent_name, skill_names, prompt in CHAT_AGENTS:
        exists = conn.execute(
            text("SELECT 1 FROM agents WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": agent_name},
        ).first()
        if exists:
            continue
        conn.execute(
            text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                    VALUES (:o, :n, :sp, :sk, :u, true, 'chat')"""),
            {"o": org_id, "n": agent_name, "sp": prompt,
             "sk": [skills[s] for s in skill_names if s in skills], "u": user_id},
        )


def ensure_chat_builtins(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Siembra las skills y agentes del chat (idempotente)."""
    ensure_chat_skills(conn, org_id, user_id)
    ensure_chat_agents(conn, org_id, user_id)
