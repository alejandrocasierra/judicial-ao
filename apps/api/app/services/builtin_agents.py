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

# Versión por defecto del system prompt del agente jurídico. Al cambiarla, la nueva versión se
# siembra en organizaciones que aún no tienen agentes; las existentes conservan su prompt editable.
DEFAULT_AGENT_VERSION = 8


def default_agent_prompt(locale: str = "es") -> str:
    """Carga el system prompt versionado del agente jurídico por defecto."""
    from app.services.legal_extraction import _load_prompt
    body, _pid, _ver = _load_prompt("agent_system", locale, version=DEFAULT_AGENT_VERSION)
    return body

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
        {"o": org_id, "n": BUILTIN_AGENT_NAME, "sp": default_agent_prompt(),
         "sk": list(skills.values()), "u": user_id},
    )


# Prompt base de Whisper (ASR), editable como agente desde /dashboard/agents.
# media_pipeline lo lee de este agente y le AÑADE por caso el radicado y los
# participantes conocidos. Si el agente no existe, se usa ASR_INITIAL_PROMPT (.env)
# y luego este default.
ASR_CONTEXT_AGENT_NAME = "Transcripción ASR · Prompt de contexto (Whisper)"
ASR_CONTEXT_DEFAULT_PROMPT = (
    "Audiencia judicial en español de Colombia. Juez, secretario, apoderado, demandante, "
    "demandado, testigo, perito, radicado."
)


def ensure_asr_context_agent(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea (idempotente por nombre) el agente que guarda el prompt base de Whisper.

    No pisa el prompt si el usuario ya lo editó: solo lo crea cuando no existe."""
    exists = conn.execute(
        text("SELECT 1 FROM agents WHERE organization_id = :o AND name = :n"),
        {"o": org_id, "n": ASR_CONTEXT_AGENT_NAME},
    ).first()
    if exists:
        return
    conn.execute(
        text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                VALUES (:o, :n, :sp, '{}', :u, true, 'system')"""),
        {"o": org_id, "n": ASR_CONTEXT_AGENT_NAME, "sp": ASR_CONTEXT_DEFAULT_PROMPT, "u": user_id},
    )


LEGAL_SKILLS: list[tuple[str, str]] = [
    ("Cita primaria", "Toda afirmacion debe resolver a una pagina o segmento del mismo expediente; sin cita no se publica."),
    ("Cifras, fechas y montos", "Compara cada cifra, fecha, monto y porcentaje con el texto citado; no redondees ni conviertas."),
    ("Partes y roles", "Valida nombres, alias y roles contra la ficha de partes; no crees partes."),
    ("Norma y jurisdiccion", "Toda ley, codigo o articulo debe existir activo en legal_rules; si falta, se bloquea."),
    ("Contradiccion en dos versiones", "Si hay dos versiones, muestra ambas o declara que no hay determinacion judicial."),
    ("Decision judicial", "Un hecho probado exige decision judicial con documento fuente."),
    ("Abstencion y limites", "Si la evidencia no alcanza, devuelve un limite explicito; no rellenes con memoria."),
    ("Respuesta independiente", "Responde sin ver la respuesta del primer modelo."),
    ("Comparacion probatoria", "Compara claims, citas, cifras y omisiones sin declarar una verdad unica."),
    ("Posicion de parte", "Distingue alega, niega y admite; no atribuyas posiciones sin cita."),
    ("Prueba admitida", "Distingue evidencia admisible de una simple alegacion."),
    ("Hecho probado", "Aplica la taxonomia: solo ADMITTED y JUDICIALLY_PROVEN son probados."),
    ("Revision humana", "Prepara el checklist de revision; no apruebes por el humano."),
]

LEGAL_AGENTS: list[tuple[str, str, list[str]]] = [
    (
        "Verificador legal",
        "Auditas claims contra fuentes primarias, partes, normas y decisiones. No escribes en la base de datos. "
        "Devuelves veredictos y hallazgos; un CRITICAL bloquea el claim.",
        ["Cita primaria", "Cifras, fechas y montos", "Partes y roles", "Norma y jurisdiccion",
         "Contradiccion en dos versiones", "Decision judicial", "Abstencion y limites"],
    ),
    (
        "Auditor de segunda opinion",
        "Respondes de forma independiente y luego comparas con la respuesta original. No ves la respuesta "
        "original en la fase 1 y no tienes tools de escritura.",
        ["Respuesta independiente", "Comparacion probatoria", "Cita primaria", "Norma y jurisdiccion"],
    ),
    (
        "Analista de hechos",
        "Extraes hechos candidatos y posiciones de parte. No declaras probado nada; solo preparas el ledger "
        "para revision humana.",
        ["Posicion de parte", "Prueba admitida", "Hecho probado", "Revision humana", "Cita primaria"],
    ),
]


def ensure_legal_agents(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea (idempotente por nombre) los agentes de los modulos legales.

    No depende de que la organizacion no tenga agentes: se ejecuta siempre y no
    pisa prompts editados.
    """
    skill_ids: dict[str, str] = {}
    for name, prompt in LEGAL_SKILLS:
        existing = conn.execute(
            text("SELECT id FROM skills WHERE organization_id=:o AND name=:n"),
            {"o": org_id, "n": name},
        ).first()
        if existing:
            skill_ids[name] = str(existing[0])
        else:
            row = conn.execute(
                text("""INSERT INTO skills (organization_id, name, system_prompt, created_by, is_system)
                        VALUES (:o,:n,:p,:u,true) RETURNING id"""),
                {"o": org_id, "n": name, "p": prompt, "u": user_id},
            ).first()
            skill_ids[name] = str(row[0])
    for name, prompt, skills in LEGAL_AGENTS:
        exists = conn.execute(
            text("SELECT 1 FROM agents WHERE organization_id=:o AND name=:n"),
            {"o": org_id, "n": name},
        ).first()
        if exists:
            continue
        conn.execute(
            text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                    VALUES (:o,:n,:p,:s,:u,true,'system')"""),
            {"o": org_id, "n": name, "p": prompt,
             "s": [skill_ids[x] for x in skills if x in skill_ids], "u": user_id},
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


# Prompts de EXTRACCIÓN gestionables desde /dashboard/agents: (nombre del agente, prompt_id,
# versión base del archivo). Al crearlos, su system_prompt es el del archivo; a partir de ahí
# el usuario puede editarlos y la extracción los leerá del agente (fallback al archivo).
EXTRACTION_AGENTS: list[tuple[str, str, int]] = [
    ("Extracción · Entidades", "extract_entities", 2),
    ("Extracción · Pretensiones", "extract_claims", 2),
    ("Extracción · Eventos", "extract_events", 2),
    ("Extracción · Actuaciones", "extract_procedural_events", 4),
    ("Extracción · Decisiones", "extract_decisions", 1),
    ("Vinculación de evidencia", "link_evidence", 1),
    ("Detección de contradicciones", "detect_contradictions", 1),
]


def ensure_extraction_agents(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Crea (idempotente por nombre) un agente por prompt de EXTRACCIÓN con su system_prompt
    del archivo base. Así TODOS los prompts de extracción quedan visibles y EDITABLES en
    /dashboard/agents. No pisa los que el usuario haya editado.

    Si un agente existe y su system_prompt es idéntico al de la versión anterior del archivo,
    lo actualiza a la versión base (migración silenciosa de prompts no editados).
    """
    from app.services.legal_extraction import _load_prompt  # perezoso (evita ciclo de import)
    for agent_name, prompt_id, version in EXTRACTION_AGENTS:
        existing = conn.execute(
            text("SELECT system_prompt FROM agents WHERE organization_id = :o AND name = :n"),
            {"o": org_id, "n": agent_name},
        ).first()
        if existing is None:
            try:
                body, _pid, _ver = _load_prompt(prompt_id, "es", version=version)
            except FileNotFoundError:
                continue
            conn.execute(
                text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                        VALUES (:o, :n, :sp, :sk, :u, true, 'extraction')"""),
                {"o": org_id, "n": agent_name, "sp": body, "sk": [], "u": user_id},
            )
            continue
        # Actualización silenciosa: si el usuario no editó (prompt == versión anterior), se lleva a la base.
        if version <= 1:
            continue
        try:
            prev_body, _pid, _ver = _load_prompt(prompt_id, "es", version=version - 1)
            new_body, _pid2, _ver2 = _load_prompt(prompt_id, "es", version=version)
        except FileNotFoundError:
            continue
        if existing.system_prompt == prev_body:
            conn.execute(
                text("UPDATE agents SET system_prompt = :sp WHERE organization_id = :o AND name = :n"),
                {"o": org_id, "n": agent_name, "sp": new_body},
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
# Protocolo de investigación compartido por los agentes que investigan el expediente
# (contrato F2 · R1). Cubre las diez secciones exigidas: rol, facetas, búsqueda
# iterativa, cobertura de fuentes, uso de hechos, contradicciones, estructura de
# redacción, prohibiciones, herramientas reales y límite de pasos.
CHAT_RESEARCH_PROTOCOL = """Eres el investigador jurídico del expediente. Respondes SOLO con evidencia del caso y distingues siempre cuatro niveles: alegación de una parte, testimonio (ASR), prueba documental y decisión judicial. Una alegación no es un hecho probado; un testimonio no es una decisión judicial.

## 1. Descompón la pregunta en facetas (antes de buscar)
(a) demanda principal y demanda acumulada / pretensiones;
(b) contestación y excepciones de mérito;
(c) interrogatorios y declaraciones en audiencia (ASR);
(d) hechos probados o admitidos (`search_facts`, `list_facts`) y contradicciones (`search_claims`, `graph_query`);
(e) autos, providencias, sentencias y procesos paralelos.
Anota qué faceta queda sin cubrir y búscala en el paso siguiente: no redactes mientras una faceta relevante siga sin revisar.

## 2. Búsqueda iterativa obligatoria
No termines después de un solo `search_case`. Para preguntas sobre partes, interrogatorios, intereses, tasas o cobros usa al menos cinco búsquedas con términos DISTINTOS: `search_case`, `search_transcripts`, `search_facts`, `search_claims` y `search_documents`. Varía los términos: números de pagaré, nombres de las partes y de los declarantes, tasas («2,5», «1,6», «1,35»), «capitalización», «usura», «interés de mora», radicados y números de proceso. Para usura o capitalización busca también «886», «Código de Comercio», «anatocismo», «Ley 45 de 1990» y «15 pagarés»; para interrogatorios, «interrogatorio de parte», «declaración», «confesión» y el nombre de quien declara. Usa `search_transcript_by_time` para volver a un minuto concreto y `get_document_page`/`read_document` para leer la página que vas a citar. Repite literalmente las cifras relevantes del expediente («15 pagarés»), no las sustituyas por «varios».

## 2b. Inventarios de títulos y objetos
Si la pregunta pide un inventario o un número de títulos («cuántos pagarés», «qué títulos», «cuántas obligaciones», «qué cheques»), no respondas con los que aparecieron en el primer resultado. Localiza la pieza que los enumera (demanda, anexo, mandamiento, cuadro de deuda), léela por páginas con `read_document`/`get_document_page` y reproduce TODOS los identificadores que consten. En una demanda con varios pagarés, los títulos suelen ocupar páginas alternas: lee el rango completo en bloques de hasta 10 páginas y enumera cada `P-...` o «PAGARE NUMERO» con su página. Si el expediente solo individualiza algunos, di cuántos constan y cita la página que los lista; nunca inventes los que faltan ni completes la lista de memoria.

## 2c. Fuentes que no pueden faltar en usura y capitalización
Para preguntas de usura, capitalización o anatocismo, busca y cita: (i) el auto que libró el mandamiento de pago y la tasa que ordenó; (ii) la grabación del interrogatorio de parte, con el minuto exacto y el hablante de la admisión sobre los pagarés capitalizados; (iii) la providencia de segunda instancia que confirma o revoca; y (iv) la norma invocada (artículo 886 del Código de Comercio, Ley 45 de 1990) tal como aparece en la fuente. Si una de esas piezas no existe o no se recupera, dilo expresamente en «Límites»; no la sustituyas por una inferencia.

## 3. Cobertura de fuentes
Revisa documentos Y audiencias. Si el expediente no tiene transcripciones o no tiene un documento concreto, dilo con esas palabras («el expediente no tiene transcripciones de audiencia»). Nunca afirmes que algo no existe porque una búsqueda no lo recuperó: primero agota las facetas.

## 4. Uso de los hechos
El contexto puede traer hechos ya extraídos con la etiqueta `[Hecho #N · PROOF_STATUS]`. Puedes apoyarte en ellos, pero cita siempre la página o el segmento que los respalda. `ALLEGED` y `DISPUTED` son posiciones de parte, no prueba. Solo `ADMITTED` y `JUDICIALLY_PROVEN` son hechos probados; `EVIDENCE_SUPPORTED` está sostenido por evidencia pero no decidido.

## 5. Contradicciones
Si hay dos versiones, presenta las dos con su fuente y di que la contradicción no está resuelta, salvo que una decisión judicial la resuelva (cítala). No elijas cuál es verdadera.

## 6. Redacción final (Markdown, en este orden)
## Síntesis
## Posición de la parte demandante
## Posición de la parte demandada
## Interrogatorios y audiencias
## Decisiones judiciales
## Fuentes
Cada afirmación lleva su cita `[E#]` o `[n]`; cuando la fuente sea ASR escribe el minuto en `mm:ss` y el hablante. En «Fuentes» lista las páginas y los segmentos usados.

## 7. Prohibiciones
- No abras con «Encontré material relacionado, pero no responde directamente a la pregunta».
- No cierres con «Se omitieron afirmaciones sin fuente verificable»: si algo no alcanza, di exactamente qué falta y qué fuente lo probaría.
- No inventes cifras, fechas, normas, nombres ni atribuciones.
- No prometas un número de pasos: el servidor fija el límite (`DEEP_MAX_STEPS`).

## 8. Adjuntos y correcciones
Si el usuario adjunta un archivo con @, léelo primero (`get_document_markdown`, `read_document`, `get_file`) antes de responder. Si pide corregir OCR/ASR o renombrar un hablante, aplica la corrección con la tool correspondiente (`confirm=true`) en el mismo turno.

## 9. Herramientas disponibles
`search_case`, `search_transcripts`, `search_transcript_by_time`, `search_facts`, `list_facts`, `get_fact`, `search_claims`, `search_documents`, `search_evidence`, `search_timeline`, `graph_query`, `graph_neighbors`, `find_person`, `list_people_by_role`, `get_document_page`, `read_document`, `get_document_markdown`, `get_timeline`, `process_path`, `event_relations`, `verify_citation`."""

# El «Cronista probatorio» conserva el protocolo y añade el orden cronológico (R1).
CHAT_CRONISTA_PROTOCOL = CHAT_RESEARCH_PROTOCOL + """

## 10. Modo cronista probatorio
Además del protocolo anterior, ordena los hitos en orden cronológico: fecha, hecho y fuente para cada uno. Marca las contradicciones entre versiones con las dos fuentes y no resuelvas por tu cuenta cuál es cierta. Si una fecha es inferida o imprecisa, dilo expresamente. Conserva la misma estructura final de secciones."""


CHAT_AGENTS: list[tuple[str, list[str], str]] = [
    ("Asistente del expediente",
     ["Analista de documento", "Analista de video (audiencias)", "Relacionador de personas",
      "Corrector de evidencia", ENCAPSULAMIENTO],
     CHAT_RESEARCH_PROTOCOL),
    ("Grill-me jurídico",
     ["Grill-me (interrogatorio jurídico)", "Cazador de contradicciones", ENCAPSULAMIENTO],
     "Interrogas al usuario como el abogado contrario: una pregunta a la vez, citando evidencia, buscando "
     "contradicciones y vacíos probatorios hasta agotar el punto. No das por cierto nada sin cita."),
    ("Cronista probatorio",
     ["Cronologista", "Cazador de contradicciones", ENCAPSULAMIENTO],
     CHAT_CRONISTA_PROTOCOL),
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
    """Crea o actualiza (idempotente por nombre) los 3 agentes del chat con sus skills.

    Los agentes del sistema son CÓDIGO, no configuración del usuario: si la fila
    existe con `is_system = true` y `kind = 'chat'`, su `system_prompt` y sus
    `skills` se llevan al valor canónico actual. Un agente del usuario con el
    mismo nombre (`is_system = false` u otro `kind`) NUNCA se toca.
    """
    skills = ensure_chat_skills(conn, org_id, user_id)
    for agent_name, skill_names, prompt in CHAT_AGENTS:
        skill_ids = [skills[s] for s in skill_names if s in skills]
        existing = conn.execute(
            text("""SELECT id, is_system, kind FROM agents
                     WHERE organization_id = :o AND name = :n"""),
            {"o": org_id, "n": agent_name},
        ).first()
        if existing is None:
            conn.execute(
                text("""INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                        VALUES (:o, :n, :sp, :sk, :u, true, 'chat')"""),
                {"o": org_id, "n": agent_name, "sp": prompt, "sk": skill_ids, "u": user_id},
            )
            continue
        # Solo se reescribe el agente de sistema del chat; el resto es del usuario.
        if not existing.is_system or existing.kind != "chat":
            continue
        conn.execute(
            text("""UPDATE agents SET system_prompt = :sp, skills = :sk
                     WHERE id = :i"""),
            {"i": existing.id, "sp": prompt, "sk": skill_ids},
        )


def ensure_chat_builtins(conn: Connection, org_id: str, user_id: str | None) -> None:
    """Siembra las skills y agentes del chat (idempotente)."""
    ensure_chat_skills(conn, org_id, user_id)
    ensure_chat_agents(conn, org_id, user_id)
