"""Genera el Manual de Usuario (.docx) de la Plataforma Judicial IA.

Pensado para una persona sin conocimientos técnicos (abogado/a): explica desde
el ingreso hasta el Chat IA, el OCR/ASR, los modelos, usuarios y el servidor MCP,
con índice, glosario, apéndices e imágenes (diagramas generados con Archify).

Uso:  python scripts/gen_manual_docx.py
Salida: docs/Manual_Usuario_Judicial_AI.docx
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Manual_Usuario_Judicial_AI.docx"
DIAG = ROOT / ".archify" / "diagrams-manual-20261004-120000"


# ---------------------------------------------------------------------------
# Utilidades de formato
# ---------------------------------------------------------------------------

def _shade(cell, color: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def callout(doc: Document, title: str, text: str, color: str = "FEF3C7") -> None:
    """Recuadro de aviso (amarillo por defecto)."""
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = t.rows[0].cells[0]
    _shade(cell, color)
    p = cell.paragraphs[0]
    r = p.add_run(title + " ")
    r.bold = True
    cell.add_paragraph(text)
    doc.add_paragraph()


def note(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic = True
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)


def bullets(doc: Document, items: list[str]) -> None:
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def numbered(doc: Document, items: list[str]) -> None:
    for it in items:
        doc.add_paragraph(it, style="List Number")


def table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        c.paragraphs[0].add_run(h).bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    doc.add_paragraph()


def add_toc(doc: Document) -> None:
    """Índice automático de Word (se actualiza al abrirlo: clic derecho → Actualizar campo)."""
    p = doc.add_paragraph()
    run = p.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    txt = OxmlElement("w:t")
    txt.text = "Haz clic derecho aquí y elige «Actualizar campo» para ver el índice."
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, sep, txt, end):
        run._r.append(el)


def image(doc: Document, name: str, caption: str, width_in: float = 6.4) -> None:
    path = DIAG / {"arq": "captures-01-arquitectura/01-arquitectura.visual-check.2048x1320.light.png",
                   "ocr": "captures-02-ocr/02-ocr.visual-check.2048x1320.light.png",
                   "asr": "captures-03-asr/03-asr.visual-check.2048x1320.light.png",
                   "chat": "captures-04-chat-mcp/04-chat-mcp.visual-check.2048x1320.light.png"}[name]
    if path.exists():
        doc.add_picture(str(path), width=Inches(width_in))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap = doc.add_paragraph(caption)
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.runs[0].italic = True
        cap.runs[0].font.size = Pt(9)
        cap.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    else:
        note(doc, f"[Diagrama no disponible: {path.name}]")


def h(doc: Document, text: str, level: int = 1):
    return doc.add_heading(text, level=level)


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------

def build() -> None:
    doc = Document()
    # Fuente base legible
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # ---------------- Portada ----------------
    for _ in range(3):
        doc.add_paragraph()
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("Plataforma Judicial IA")
    r.bold = True
    r.font.size = Pt(34)
    s = doc.add_paragraph()
    s.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = s.add_run("Manual de Usuario")
    r2.font.size = Pt(22)
    r2.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    doc.add_paragraph()
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run("Guía paso a paso para abogados y equipos jurídicos\n"
                "Expedientes, OCR, audiencias, Chat IA y evidencia verificable").italic = True
    for _ in range(6):
        doc.add_paragraph()
    d = doc.add_paragraph()
    d.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_run(f"Versión 1.0 · {_dt.date.today().strftime('%d/%m/%Y')}")
    conf = doc.add_paragraph()
    conf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    conf.add_run("Documento de uso interno · Contiene información confidencial del despacho").italic = True
    doc.add_page_break()

    # ---------------- Índice ----------------
    h(doc, "Índice", 1)
    indice = [
        ("Cómo leer este manual", 1),
        ("1. Ingreso a la plataforma", 1),
        ("2. El panel de navegación", 1),
        ("3. Crear y organizar un proceso", 1),
        ("3.1 Crear el proceso", 2), ("3.2 Carpetas y subcarpetas", 2), ("3.3 Subir archivos", 2),
        ("3.4 ¿Dónde se guardan los archivos?", 2),
        ("4. El proceso de OCR (lectura de documentos)", 1),
        ("4.1 Modo «Básico» (lectura en el propio servidor)", 2),
        ("4.2 Modo «Document AI» (motor de Google)", 2),
        ("4.3 Confianza, folios y corrección", 2),
        ("5. El proceso de ASR (audiencias y videos)", 1),
        ("5.1 Cómo funciona", 2), ("5.2 Requisitos", 2),
        ("6. Modelos de IA (inteligencia artificial)", 1),
        ("6.1 Crear un modelo", 2),
        ("7. Agentes y Skills", 1),
        ("7.1 Agentes incluidos", 2), ("7.2 Skills incluidas", 2), ("7.3 Crear tu propio agente o skill", 2),
        ("8. Usuarios, permisos y correo", 1),
        ("8.1 Correo saliente (SMTP)", 2),
        ("9. Chat IA con tu expediente", 1),
        ("9.1 Cómo usarlo", 2), ("9.2 Qué puedes preguntar", 2),
        ("9.3 Citas, tarjetas y correcciones", 2), ("9.4 Memoria y conversaciones", 2),
        ("10. Servidor MCP (conexión con otros programas)", 1),
        ("11. Garantías de legalidad y evidencia", 1),
        ("Apéndices", 1),
        ("A. Glosario de términos", 2), ("B. Preguntas frecuentes", 2),
        ("C. Permisos por rol (resumen)", 2), ("D. Detalles técnicos del OCR y del ASR", 2),
        ("E. Instalación y puesta en marcha (para soporte)", 2), ("F. Solución de problemas", 2),
    ]
    for titulo, nivel in indice:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        if nivel == 2:
            p.paragraph_format.left_indent = Inches(0.35)
        r = p.add_run(titulo)
        r.bold = (nivel == 1)
        r.font.size = Pt(11 if nivel == 1 else 10)
        if nivel == 2:
            r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    doc.add_paragraph()
    note(doc, "¿Quieres el índice con NÚMEROS DE PÁGINA? En Word: haz clic derecho justo debajo y elige "
              "«Actualizar campo» → «Actualizar toda la tabla».")
    add_toc(doc)
    doc.add_page_break()

    # ---------------- Cómo leer este manual ----------------
    h(doc, "Cómo leer este manual", 1)
    doc.add_paragraph(
        "Este manual está escrito para personas que trabajan con expedientes judiciales y NO necesitan "
        "saber de tecnología. Cada capítulo explica, en lenguaje sencillo, para qué sirve cada parte de "
        "la plataforma, cómo se usa y qué ocurre «por dentro» sin tecnicismos innecesarios. Cuando aparece "
        "una palabra técnica, se explica en el momento y también en el Glosario (Apéndice A)."
    )
    bullets(doc, [
        "Los recuadros amarillos resaltan avisos importantes sobre legalidad y seguridad.",
        "Los textos en cursiva gris son aclaraciones para quien quiera profundizar.",
        "Al final hay apéndices: glosario, preguntas frecuentes, permisos y detalles técnicos.",
    ])
    callout(doc, "Principio de oro:",
            "La plataforma NUNCA inventa texto. El OCR (lectura de documentos) y el ASR (transcripción de "
            "audiencias) reproducen lo que está escrito o lo que se dijo. Si algo no está en el expediente, "
            "el sistema lo dice claramente en lugar de suponerlo. Todas las respuestas del Chat IA citan la "
            "fuente exacta: documento y página, o video y minuto.", color="DBEAFE")

    image(doc, "arq", "Figura 1. Visión general: cómo se conectan el portal, la base de datos, el "
                      "procesamiento de documentos/videos y la IA.")
    doc.add_page_break()

    # ---------------- 1. Ingreso ----------------
    h(doc, "1. Ingreso a la plataforma", 1)
    doc.add_paragraph(
        "El acceso es mediante correo electrónico y contraseña. Cada usuario pertenece a una organización "
        "(por ejemplo, el despacho o la firma) y solo ve la información de su organización."
    )
    h(doc, "Cómo entrar", 2)
    numbered(doc, [
        "Abre el navegador (Chrome, Edge o Safari) y entra a la dirección de la plataforma.",
        "Escribe tu correo y tu contraseña y pulsa «Entrar».",
        "Si olvidaste la contraseña, usa el enlace de recuperación; recibirás un correo para crear una nueva.",
    ])
    callout(doc, "Seguridad de la sesión:",
            "La sesión usa un identificador temporal (JWT) que caduca automáticamente. Si se vence, la "
            "plataforma te pide volver a entrar sin perder tu trabajo. Nunca compartas tu contraseña: cada "
            "acción queda registrada con el usuario que la realizó.", color="E0E7FF")

    # ---------------- 2. El panel ----------------
    h(doc, "2. El panel de navegación", 1)
    doc.add_paragraph("A la izquierda verás el menú con los módulos principales:")
    table(doc, ["Módulo", "Para qué sirve"], [
        ["Procesos", "Crear y organizar expedientes, carpetas y archivos."],
        ["PDFs", "Abrir cuadernillos, revisar el texto reconocido (OCR) y corregirlo por hoja."],
        ["Videos", "Reproducir audiencias y revisar la transcripción con hablantes y minutos."],
        ["Chats IA", "Historial de conversaciones del Chat IA y acceso a cada una."],
        ["Agentes", "Crear asistentes especializados (por ejemplo, un interrogador jurídico)."],
        ["Skills", "Habilidades reutilizables que se asignan a los agentes."],
        ["Modelos IA", "Configurar proveedor de IA, API key y modelo."],
        ["Usuarios / Roles", "Gestionar personas, permisos y roles."],
        ["Ajustes", "Correo saliente, respaldos y configuración general."],
    ])
    note(doc, "En la esquina inferior derecha de todas las pantallas está el botón del Chat IA (un ícono de "
              "conversación). Lo explicamos en el capítulo 9.")

    # ---------------- 3. Crear un proceso ----------------
    h(doc, "3. Crear y organizar un proceso", 1)
    doc.add_paragraph(
        "Un «proceso» es el expediente. Dentro se guardan los documentos y videos, organizados en carpetas "
        "y subcarpetas, como en el explorador de archivos de tu computador."
    )
    h(doc, "3.1 Crear el proceso", 2)
    numbered(doc, [
        "Ve a «Procesos» y pulsa «Nuevo proceso».",
        "Escribe el número de radicación (los 23 dígitos), un título descriptivo y el idioma.",
        "Elige la jurisdicción (por defecto, Colombia). Pulsa «Guardar».",
    ])
    h(doc, "3.2 Carpetas y subcarpetas", 2)
    bullets(doc, [
        "Dentro del proceso, pulsa «Nueva carpeta» para crear la estructura (por ejemplo, «Demanda», «Anexos», «Audiencias»).",
        "Con la carpeta abierta, vuelve a pulsar «Nueva carpeta» para crear una subcarpeta dentro.",
        "Puedes renombrar o mover archivos entre carpetas como en cualquier gestor de archivos.",
    ])
    h(doc, "3.3 Subir archivos", 2)
    doc.add_paragraph(
        "Puedes subir archivos de tres formas. Los PDF y los videos pasan por un procesamiento automático "
        "(OCR y ASR); las hojas de cálculo y otros formatos se guardan como archivos de apoyo del expediente."
    )
    table(doc, ["Tipo de subida", "Qué acepta", "Qué ocurre"], [
        ["Excel / índice", "Archivos .xlsx, .csv y similares usados como índice o control del despacho",
         "Se guardan como archivo de apoyo. No requieren lectura automática."],
        ["Documento individual", "Un PDF, imagen o documento (por ejemplo, la demanda)",
         "Se reconoce el texto (OCR) y queda buscable."],
        ["Subida masiva", "Muchos PDF de una vez (arrastrar o seleccionar varios)",
         "Se procesan en cola, uno tras otro, sin bloquear el portal."],
        ["Video / audio", "MP4, WAV y otros formatos de audiencia",
         "Se transcribe (ASR), se separan las voces y se marcan los minutos."],
    ])
    callout(doc, "Los originales nunca se alteran:", "El archivo que subes se guarda intacto con una huella "
            "digital (sha256) que permite demostrar que no cambió. La plataforma no permite borrar documentos "
            "desde la interfaz: la evidencia es inmutable.", color="DCFCE7")

    h(doc, "3.4 ¿Dónde se guardan los archivos?", 2)
    doc.add_paragraph(
        "Los archivos originales (PDF, videos, imágenes, hojas de cálculo) se guardan en un almacenamiento "
        "seguro de objetos, identificados por una huella digital (no por su nombre). El sistema puede guardarlos "
        "de dos formas, y para ti es transparente:"
    )
    bullets(doc, [
        "En el propio servidor del despacho.",
        "En la nube de Google (Google Cloud Storage), en un espacio privado del despacho. Es lo habitual en "
        "producción, para no depender del disco del servidor.",
    ])
    doc.add_paragraph(
        "En cualquier caso, los archivos son privados: se accede a ellos solo a través de la plataforma, con tu "
        "sesión y permisos. No cambia nada de tu trabajo: el visor de PDF, el video y las descargas funcionan "
        "igual, y el Chat IA sigue leyendo el texto (OCR/ASR) que ya está en la base del expediente."
    )
    callout(doc, "El video no se descarga completo para verlo:",
            "Al abrir una audiencia, el reproductor trae solo la parte que estás viendo (streaming), por lo que "
            "abre rápido y salta directo al minuto que elijas, aunque el video pese cientos de MB.", color="DBEAFE")

    doc.add_page_break()
    # ---------------- 4. OCR ----------------
    h(doc, "4. El proceso de OCR (lectura de documentos)", 1)
    doc.add_paragraph(
        "OCR significa «Reconocimiento Óptico de Caracteres»: convertir la imagen de una página en texto "
        "digital que se puede buscar, citar y analizar. La plataforma ofrece DOS modos y puedes elegir cuál "
        "usar por documento."
    )
    image(doc, "ocr", "Figura 2. Proceso OCR: desde la subida del PDF hasta la revisión humana por hoja.")

    h(doc, "4.1 Modo «Básico» (lectura en el propio servidor)", 2)
    doc.add_paragraph(
        "El motor local lee el documento dentro de la plataforma, sin enviarlo a servicios externos. Es "
        "privado y no depende de internet."
    )
    bullets(doc, [
        "Tecnologías usadas: Docling (organización de la página), RapidOCR o Tesseract según configuración, "
        "preprocesado de imagen con OpenCV y corrección de inclinación (deskew), y un modelo opcional para "
        "letra manuscrita.",
        "Cómo obtiene la información: convierte cada página en imagen, detecta las líneas y palabras, y "
        "reconstruye el texto conservando el orden de lectura; reconoce también el folio (número de hoja).",
        "Confianza: cada página guarda un porcentaje de confianza. Las hojas por debajo del umbral "
        "(configurable, por defecto 85 %) se marcan para revisión.",
    ])
    h(doc, "4.2 Modo «Document AI» (motor de Google)", 2)
    doc.add_paragraph(
        "Para escaneos difíciles (manchas, sellos, formularios), la plataforma puede usar Google Document AI, "
        "un servicio especializado de Google Cloud. Requiere credenciales de Google configuradas por el "
        "administrador."
    )
    bullets(doc, [
        "Tecnología: Google Document AI, con autenticación segura por cuenta de servicio.",
        "Cómo obtiene la información: procesa el PDF por tandas, devuelve texto, bloques y casillas con su "
        "nivel de confianza por elemento.",
        "Ventaja: mayor acierto en documentos complejos; se puede reprocesar un documento con este motor si "
        "el modo Básico no fue suficiente.",
    ])
    h(doc, "4.3 Confianza, folios y corrección", 2)
    table(doc, ["Concepto", "Qué significa para tu trabajo"], [
        ["Confianza de la hoja", "Porcentaje de seguridad de la lectura. Bajo = conviene revisar."],
        ["Folio", "Número de hoja del cuaderno que detecta la plataforma; útil para citar «folio 12»."],
        ["Revisión (needs_review)", "La hoja queda marcada para que una persona la compruebe."],
        ["Corrección humana", "Si corriges una hoja, el texto mejora, se reindexa la búsqueda y se "
                              "actualiza el grafo. Se guarda el original y la corrección (registro de revisión)."],
    ])
    callout(doc, "Sobre la exactitud:", "El OCR transcribe lo que la imagen muestra. No «adivina» palabras ni "
            "inventa números: si un carácter no se reconoce con seguridad, el sistema baja la confianza de la "
            "hoja y la marca para revisión en lugar de completarla por su cuenta.", color="FEF3C7")
    doc.add_page_break()

    # ---------------- 5. ASR ----------------
    h(doc, "5. El proceso de ASR (audiencias y videos)", 1)
    doc.add_paragraph(
        "ASR significa «Reconocimiento Automático del Habla»: convertir el audio de una audiencia o video en "
        "texto con marcas de tiempo, indicando quién habló y en qué minuto."
    )
    image(doc, "asr", "Figura 3. Proceso ASR: audio, transcripción, separación de voces y revisión.")
    h(doc, "5.1 Cómo funciona", 2)
    numbered(doc, [
        "Se extrae el audio del video (herramienta FFmpeg), sin modificar el archivo original.",
        "Se transcribe el audio con un motor de reconocimiento (faster-whisper), que devuelve texto con "
        "marcas de tiempo por frase.",
        "Se separan las voces por hablante (diarización con pyannote) para saber cuántas personas intervienen.",
        "Se intenta identificar el nombre de cada hablante (por ejemplo, con la identificación visual de la "
        "videollamada) y se generan segmentos: «minuto 14:32 · Juan Pérez · …».",
        "Los segmentos con baja confianza se marcan para revisión y se pueden corregir a mano.",
    ])
    h(doc, "5.2 Requisitos", 2)
    bullets(doc, [
        "El audio o video debe tener sonido audible (no sirve un archivo sin pista de audio).",
        "Para separar voces puede requerirse una clave de Hugging Face (la configura el administrador).",
        "En equipos sin tarjeta gráfica, la transcripción funciona en el procesador (más lenta pero correcta).",
    ])
    callout(doc, "Sobre la fidelidad:", "La transcripción reproduce lo que se oye. Si una palabra es dudosa, "
            "queda con baja confianza y marcada para revisión; el sistema no sustituye ni completa frases por su "
            "cuenta. Las correcciones que hagas quedan registradas y mejoran las siguientes búsquedas.",
            color="FEF3C7")
    doc.add_page_break()

    # ---------------- 6. Modelos ----------------
    h(doc, "6. Modelos de IA (inteligencia artificial)", 1)
    doc.add_paragraph(
        "La IA de la plataforma es intercambiable. Puedes usar el proveedor que prefieras y pegar tu propia "
        "API key. Se configura en «Modelos IA»."
    )
    h(doc, "6.1 Crear un modelo", 2)
    numbered(doc, [
        "Ve a «Modelos IA» y pulsa «Nuevo modelo».",
        "Elige el proveedor (Anthropic, OpenAI, Google Gemini, Kimi, DeepSeek o Personalizado).",
        "Elige el modelo del catálogo o escribe su nombre si es nuevo.",
        "Pega tu API key. Marca «modelo por defecto» si quieres que se use siempre.",
        "Opcional: si usas un intermediario, otra región o tu propio servidor, escribe la «URL base del API».",
        "Pulsa «Guardar».",
    ])
    table(doc, ["Proveedor", "Para qué suele usarse"], [
        ["Anthropic (Claude)", "Redacción y razonamiento jurídico de alta calidad."],
        ["OpenAI", "Uso general y modelos económicos para tareas repetitivas."],
        ["Google Gemini", "Alternativa con buena relación calidad/precio."],
        ["Kimi (Moonshot)", "Modelos con ventanas de contexto amplias."],
        ["DeepSeek", "Alternativa económica para tareas de extracción."],
        ["Personalizado", "Cualquier servicio compatible con el estándar de OpenAI (incluido tu propio servidor)."],
    ])
    callout(doc, "Sobre las URL:", "El administrador puede cambiar la dirección de cada proveedor sin tocar el "
            "código, ya sea en la configuración del servidor o por modelo (campo «URL base del API»). Esto "
            "facilita mover la plataforma a un servidor propio (por ejemplo, una máquina en Google Cloud) y "
            "seguir conectando con todos los proveedores.", color="DBEAFE")
    note(doc, "Solo un modelo puede estar marcado para OCR y uno para ASR por organización; al activarlo en "
              "un modelo, se desmarca en los demás. Esto lo usa el lector automático de documentos.")

    # ---------------- 7. Agentes y Skills ----------------
    h(doc, "7. Agentes y Skills", 1)
    doc.add_paragraph(
        "Un «agente» es un asistente con una personalidad y unas instrucciones. Una «Skill» es una habilidad "
        "reutilizable que se le asigna a un agente. La plataforma trae agentes y skills ya creados, y puedes "
        "crear los tuyos."
    )
    h(doc, "7.1 Agentes incluidos", 2)
    table(doc, ["Agente", "Para qué sirve"], [
        ["Asistente del expediente", "Responde preguntas sobre documentos, audiencias y personas del caso."],
        ["Grill-me jurídico", "Te interroga como la contraparte: una pregunta a la vez, buscando contradicciones."],
        ["Cronista probatorio", "Construye la cronología y las contradicciones con su cita."],
        ["Agente jurídico (anti-alucinación)", "Asistente general que solo responde con evidencia citada."],
        ["Agente OCR / Agente ASR", "Supervisan la lectura de documentos y la transcripción de audiencias."],
    ])
    h(doc, "7.2 Skills incluidas", 2)
    bullets(doc, [
        "Encapsulamiento (siempre activa): solo responde con datos del expediente, nunca de internet.",
        "Interrogatorio jurídico: pregunta una cosa a la vez citando evidencia.",
        "Analista de documento: lee el archivo adjunto por páginas y cita la página.",
        "Analista de video: responde en qué minuto se dijo algo y quién lo dijo.",
        "Corrector de evidencia: propone correcciones y pide tu confirmación antes de aplicarlas.",
        "Cronologista, Cazador de contradicciones y Relacionador de personas.",
    ])
    h(doc, "7.3 Crear tu propio agente o skill", 2)
    numbered(doc, [
        "En «Skills», pulsa «Nueva skill», escribe un nombre y el texto de instrucciones. Guarda.",
        "En «Agentes», pulsa «Nuevo agente», escribe su nombre y sus instrucciones.",
        "Marca las skills que quieras asignarle. Guarda.",
        "En el Chat IA, escribe «/» y elige tu agente para conversar con él.",
    ])

    # ---------------- 8. Usuarios, permisos y correo ----------------
    h(doc, "8. Usuarios, permisos y correo", 1)
    doc.add_paragraph(
        "Cada persona accede con su propio usuario. Los permisos dependen del rol de la persona en la "
        "organización y de su rol en cada expediente."
    )
    table(doc, ["Rol de organización", "Qué puede hacer"], [
        ["Administrador (ORG_ADMIN)", "Todo dentro de su organización."],
        ["Gestor de casos", "Crear y administrar expedientes, documentos y miembros."],
        ["Abogado", "Trabajar con expedientes, subir documentos y usar el Chat IA."],
        ["Revisor", "Revisar y aprobar; puede consultar la IA."],
        ["Analista", "Solo lectura y consultas a la IA."],
        ["Solo lectura", "Ver, sin cambios."],
    ])
    h(doc, "8.1 Correo saliente (SMTP)", 2)
    doc.add_paragraph(
        "En «Ajustes» se configura el correo del despacho. Se usa para invitar usuarios y restablecer "
        "contraseñas. Debes indicar el servidor, el puerto, la seguridad (por ejemplo, STARTTLS), usuario y "
        "contraseña del correo."
    )
    callout(doc, "Invitaciones:", "Al crear un usuario se le envía un correo con un enlace para definir su "
            "contraseña. Revisa la carpeta de spam si no llega.", color="E0E7FF")

    # ---------------- 9. Chat IA ----------------
    doc.add_page_break()
    h(doc, "9. Chat IA con tu expediente", 1)
    doc.add_paragraph(
        "El Chat IA es un asistente que responde sobre TU expediente usando lo que ya está indexado: el texto "
        "de los documentos (OCR), la transcripción de las audiencias (ASR), la búsqueda semántica y el grafo "
        "de relaciones. Cada respuesta incluye las citas exactas."
    )
    image(doc, "chat", "Figura 4. Cómo se conecta el Chat IA: pregunta, herramientas del caso, verificación "
                       "de evidencia y respuesta con citas.")
    h(doc, "9.1 Cómo usarlo", 2)
    numbered(doc, [
        "Pulsa el botón del Chat IA (abajo a la derecha).",
        "Escribe tu pregunta en lenguaje natural.",
        "Para elegir un agente, escribe «/» y selecciónalo (aparece como una etiqueta que puedes quitar).",
        "Para adjuntar un archivo, escribe «@» y elige el documento o video; aparece como una etiqueta con su "
        "identificador real (no es texto suelto).",
        "Pulsa enviar. Verás «Buscando en el expediente…» mientras trabaja.",
    ])
    h(doc, "9.2 Qué puedes preguntar", 2)
    bullets(doc, [
        "«¿Qué dice el documento @001 sobre la demanda?» → lee el OCR y cita la página.",
        "«¿En qué minuto se habló de la caución y quién lo dijo?» → responde con el minuto y el hablante.",
        "«¿Quién es Juan Pérez en este proceso?» → personas, hechos y relaciones del grafo.",
        "«Tráeme el PDF del cuaderno principal» → muestra una tarjeta con botones Ver y Descargar.",
        "«¿Hay contradicciones sobre el pago?» → las señala y pide revisión humana.",
    ])
    h(doc, "9.3 Citas, tarjetas y correcciones", 2)
    bullets(doc, [
        "Cita de documento: muestra «documento p.12»; al pulsarla abre el visor en esa página.",
        "Cita de video: muestra «video 14:32 · hablante»; al pulsarla abre el video en ese segundo.",
        "Tarjeta de archivo: botones «Ver» (abre el documento o video) y «Descargar».",
        "Corrección: si algo está mal, díselo con tus palabras (por ejemplo, «en esa página en realidad "
        "dice…»). El chat propone el cambio y muestra botones «Confirmar» y «Cancelar». Solo se aplica si "
        "confirmas.",
    ])
    h(doc, "9.4 Memoria, conversaciones y velocidad", 2)
    doc.add_paragraph(
        "Cada expediente tiene conversaciones guardadas. El chat recuerda los últimos turnos, así que puedes "
        "preguntar «¿y quién más estaba?» y entiende que sigues hablando de lo mismo. En el selector de arriba "
        "del chat:"
    )
    bullets(doc, [
        "«Nueva conversación»: empieza un tema nuevo sin perder el anterior; la conversación previa queda "
        "guardada en la lista.",
        "Cada conversación se acumula en el selector (con su título y número de mensajes).",
        "El botón «Eliminar» (en «Chats IA» o el ícono junto al selector) la retira del historial. No afecta "
        "a los documentos ni a la evidencia del expediente.",
    ])
    doc.add_paragraph(
        "Las respuestas muestran la cita exacta: la página del documento o el minuto del video con el hablante. "
        "Al pulsar una cita de video, se abre el reproductor y salta a ese minuto; el video se reproduce por "
        "«streaming», así que abre rápido aunque sea largo."
    )
    callout(doc, "Regla anti-invención:", "Antes de responder, el sistema busca la evidencia y verifica que "
            "cada frase esté respaldada por un documento o por un minuto de la audiencia. Si una frase no tiene "
            "respaldo, se descarta y no se muestra. Si no hay evidencia suficiente, el chat lo dice en lugar de "
            "suponer. El chat NO busca en internet ni usa conocimiento externo al expediente.", color="DBEAFE")

    # ---------------- 10. MCP ----------------
    h(doc, "10. Servidor MCP (conexión con otros programas)", 1)
    doc.add_paragraph(
        "MCP es un estándar que permite que otros asistentes de IA (como Claude Code o Cursor) usen las mismas "
        "herramientas del expediente: buscar, leer páginas, consultar el grafo, traer archivos y proponer "
        "correcciones. Cada llamada respeta tus permisos y tu organización."
    )
    bullets(doc, [
        "Dirección: http://<servidor>:8100/mcp (transporte HTTP).",
        "Autenticación: el mismo identificador de sesión (token) de la plataforma.",
        "Herramientas: las mismas del Chat IA, en modo lectura y corrección con confirmación.",
        "Recursos: puedes pedir la lista de archivos del caso, estadísticas del grafo y el texto de una página.",
    ])
    note(doc, "El servidor MCP se activa con el servicio «mcp» de la instalación. Detalles técnicos en el "
              "Apéndice E.")

    # ---------------- 11. Garantías legales ----------------
    h(doc, "11. Garantías de legalidad y evidencia", 1)
    doc.add_paragraph(
        "Estas garantías son el corazón de la plataforma y están integradas en el diseño, no son opcionales."
    )
    table(doc, ["Garantía", "Qué asegura"], [
        ["Nada se inventa", "El OCR/ASR transcriben; la IA solo cita lo que existe en el expediente."],
        ["Toda afirmación tiene cita", "Documento + página, o video + minuto. Sin cita, no se muestra."],
        ["Originales inmutables", "Los archivos no se alteran ni se borran; se guarda su huella digital."],
        ["Corrección con control", "Toda corrección humana queda registrada (original y corrección) y auditada."],
        ["Aislamiento por organización", "Nadie ve información de otra firma u organización."],
        ["Trazabilidad", "Cada consulta y cambio queda en el registro de auditoría."],
        ["Alegación ≠ hecho", "Una alegación no se trata como hecho; las contradicciones piden revisión humana."],
    ])
    callout(doc, "Para tu tranquilidad:", "Aunque la IA redacte, la fuente siempre son tus documentos y tus "
            "audiencias. El sistema está diseñado para preferir «no lo sé» antes que inventar.", color="DCFCE7")

    # ---------------- Apéndices ----------------
    doc.add_page_break()
    h(doc, "Apéndices", 1)

    h(doc, "Apéndice A. Glosario de términos", 2)
    glosario = [
        ("IA (Inteligencia Artificial)", "Programa que puede leer, resumir y redactar. En esta plataforma solo "
         "trabaja con la información del expediente."),
        ("Modelo / LLM", "El «motor» de IA (por ejemplo, Claude o GPT). Se elige en «Modelos IA»."),
        ("API key", "La «llave» que autoriza usar un servicio de IA. Es secreta."),
        ("OCR", "Reconocimiento de caracteres: convierte la imagen de una página en texto buscable."),
        ("ASR", "Reconocimiento del habla: convierte el audio de una audiencia en texto con minutos."),
        ("Diarización", "Separar las voces para saber cuántas personas hablan y quién dijo qué."),
        ("Folio", "El número de hoja del cuaderno; ayuda a citar con precisión."),
        ("Confianza", "Porcentaje de seguridad del reconocimiento. Bajo = conviene revisar."),
        ("Embeddings / pgvector", "Forma de buscar por significado (no solo por palabra exacta)."),
        ("Grafo de conocimiento", "Mapa de personas, hechos, eventos y sus relaciones."),
        ("Cita / evidencia", "La referencia exacta que respalda una afirmación."),
        ("Anti-alucinación (grounding)", "Mecanismo que descarta afirmaciones sin respaldo."),
        ("Token / sesión (JWT)", "Identificador temporal de tu sesión; caduca por seguridad."),
        ("Aislamiento (RLS)", "Regla que impide ver datos de otra organización."),
        ("Contenedor / Docker", "Forma empaquetada de instalar la plataforma en un servidor."),
        ("MCP", "Estándar para que otros asistentes de IA usen las herramientas del expediente."),
        ("Worker / cola", "Proceso que realiza el OCR/ASR en segundo plano, sin bloquear el portal."),
    ]
    for term, defn in glosario:
        p = doc.add_paragraph()
        p.add_run(term + ": ").bold = True
        p.add_run(defn)

    h(doc, "Apéndice B. Preguntas frecuentes", 2)
    faq = [
        ("¿La IA puede inventar una cita o un monto?",
         "No. Cada afirmación debe estar respaldada por un documento o por un minuto de la audiencia. Si no "
         "hay respaldo, la frase se descarta y no aparece en la respuesta."),
        ("¿El OCR/ASR completan palabras que no se ven o no se oyen?",
         "No. Reproducen lo que hay. Si algo es dudoso, bajan la confianza y lo marcan para revisión."),
        ("¿Puedo corregir un error del OCR o de la transcripción?",
         "Sí. En el visor (o desde el chat) corriges el texto; el cambio se registra, mejora el diccionario y "
         "actualiza la búsqueda y el grafo."),
        ("¿Se pierde la versión anterior si corrijo?",
         "No. Se conserva el texto original y la corrección (registro de revisión, solo se agrega)."),
        ("¿La plataforma busca en internet?",
         "No. Solo usa el expediente. Nunca usa conocimiento externo ni de la web."),
        ("¿Puedo usar mi propia API key de IA?",
         "Sí. En «Modelos IA» pegas tu clave del proveedor que prefieras."),
        ("¿Qué pasa si cambio de servidor o me mudo a una nube?",
         "Las direcciones se configuran en un archivo de entorno y, por modelo, en el campo «URL base del API». "
         "No hay que tocar el código."),
    ]
    for q, a in faq:
        p = doc.add_paragraph()
        p.add_run("P: " + q).bold = True
        doc.add_paragraph("R: " + a)

    h(doc, "Apéndice C. Permisos por rol (resumen)", 2)
    table(doc, ["Acción", "Adm.", "Gestor", "Abogado", "Revisor", "Analista", "Solo lectura"], [
        ["Ver expedientes", "Sí", "Sí", "Sí", "Sí", "Sí", "Sí"],
        ["Crear/editar expedientes", "Sí", "Sí", "Sí", "No", "No", "No"],
        ["Subir documentos", "Sí", "Sí", "Sí", "No", "No", "No"],
        ["Corregir OCR/ASR", "Sí", "Sí", "Sí", "No", "No", "No"],
        ["Usar el Chat IA", "Sí", "Sí", "Sí", "Sí", "Sí", "No"],
        ["Revisar/aprobar", "Sí", "Sí", "Sí", "Sí", "No", "No"],
        ["Administrar usuarios", "Sí", "No", "No", "No", "No", "No"],
    ])
    note(doc, "Los permisos también pueden variar por expediente (rol en el caso: propietario, abogado, "
              "revisor, observador).")

    h(doc, "Apéndice D. Detalles técnicos del OCR y del ASR", 2)
    doc.add_paragraph("Para el área de sistemas. No es necesario para el uso diario.")
    table(doc, ["Etapa", "Tecnología usada", "Nota"], [
        ["Lectura de PDF (Básico)", "Docling / RapidOCR / Tesseract; OpenCV y deskew", "Local, sin enviar el documento fuera."],
        ["Lectura avanzada", "Google Document AI", "Requiere credenciales de Google Cloud."],
        ["Campos de formularios", "Modelo de IA (prompt versionado) opcional", "Desactivado por defecto."],
        ["Voz → texto", "faster-whisper", "CPU o GPU; devuelve marcas de tiempo."],
        ["Separación de voces", "pyannote.audio", "Puede requerir clave de Hugging Face."],
        ["Audio de video", "FFmpeg / ffmpeg de imagenio", "Solo extrae el audio; no altera el original."],
        ["Búsqueda por significado", "pgvector (PostgreSQL)", "Base de datos con vectores."],
        ["Grafo", "Tablas de nodos y relaciones", "Personas, hechos, eventos, decisiones."],
        ["Servidor MCP", "SDK MCP + JWT", "Expone las mismas herramientas del chat."],
    ])

    h(doc, "Apéndice E. Instalación y puesta en marcha (para soporte)", 2)
    numbered(doc, [
        "Instala Docker en el servidor (o en tu equipo).",
        "Copia el archivo de configuración de ejemplo (.env.example) a .env y ajusta las direcciones, "
        "contraseñas y las URLs de los proveedores de IA.",
        "Levanta los servicios: docker compose up -d --build (incluye la base de datos, el portal, el "
        "procesador y el servidor MCP).",
        "Abre el portal en el navegador, crea la primera cuenta y configura el correo y los modelos de IA.",
        "Opcional (recomendado en producción): activar el almacenamiento en Google Cloud Storage indicando el "
        "bucket, la carpeta del ambiente y las credenciales de servicio; así los archivos no viven en el disco "
        "del servidor.",
    ])
    note(doc, "Si mañana mueves la plataforma a un servidor en Google u otra nube, solo cambias las direcciones "
              "en .env (y, si lo prefieres, la «URL base del API» de cada modelo). El resto funciona igual.")

    h(doc, "Apéndice F. Solución de problemas", 2)
    table(doc, ["Síntoma", "Qué revisar"], [
        ["El chat responde «evidencia insuficiente»", "El documento aún se está procesando (OCR/ASR) o la "
         "pregunta no aparece en el expediente."],
        ["Una página salió con texto extraño", "Ábrela en el visor PDFs, corrígela, o reprocesa el documento "
         "con el otro motor (Document AI / Básico)."],
        ["No se reconocen los hablantes del video", "Puede faltar la clave de Hugging Face o el audio es de "
         "baja calidad."],
        ["No puedo ver información de otro expediente", "Es correcto: el aislamiento por organización y por "
         "expediente lo impide."],
        ["La IA no responde o da error de clave", "Revisa la API key del modelo en «Modelos IA»."],
    ])

    doc.add_paragraph()
    fin = doc.add_paragraph()
    fin.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fin.add_run("— Fin del manual —").italic = True
    doc.add_paragraph()
    note(doc, "Este manual se puede actualizar cuando cambien los flujos. Los diagramas fueron generados con "
              "Archify a partir del sistema real.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(str(OUT))
        target = OUT
    except PermissionError:
        # El archivo está abierto en Word: guarda una versión nueva sin sobrescribir.
        target = OUT.with_name(f"{OUT.stem}_v{_dt.datetime.now():%Y%m%d_%H%M}.docx")
        doc.save(str(target))
    print(f"[manual] escrito: {target}")


if __name__ == "__main__":
    build()
