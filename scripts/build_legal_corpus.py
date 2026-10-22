"""Construye el corpus legal colombiano desde fuentes oficiales.

No usa memoria del modelo ni texto de terceros: descarga las páginas oficiales
de Secretaría del Senado, Función Pública y DIAN, extrae el bloque de cada
artículo pedido (incluye numeración punteada) y calcula el SHA-256 del texto
normalizado. El resultado es determinista para una misma versión publicada.

Uso:
  python scripts/build_legal_corpus.py --dry-run
  python scripts/build_legal_corpus.py
  python scripts/build_legal_corpus.py --only ley_1579_2012,cgp
  python scripts/build_legal_corpus.py --refresh --only decreto_525_2009

El importador (`scripts/import_legal_rules.py`) consume los JSON generados.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "legal_rules" / "co"
CACHE_DIR = ROOT / ".cache" / "legal_corpus"
BASEDOC = "http://www.secretariasenado.gov.co/senado/basedoc"
USER_AGENT = "JudicialAI-corpus-builder/1.0 (+https://judicial.ai)"
TIMEOUT = 45
REFRESH = False

# Catálogo de normas del expediente piloto. Solo se listan artículos que el
# expediente cita de forma explícita (ver docs/AUDITORIA_MODULOS_LEGALES_2026-10-22.md).
SOURCES: list[dict] = [
    {
        "key": "ley_1579_2012",
        "index": f"{BASEDOC}/ley_1579_2012.html",
        "source": "Ley 1579 de 2012 - Estatuto de Registro de Instrumentos Públicos",
        "law_name": "Ley 1579 de 2012",
        "output": "ley_1579_2012.json",
        "articles": ["3", "4", "8", "10", "11", "12", "13", "14", "15", "16",
                     "22", "28", "31", "33", "34", "59"],
    },
    {
        "key": "decreto_2723_2014",
        "index": f"{BASEDOC}/decreto_2723_2014.html",
        "source": "Decreto 2723 de 2014 - Superintendencia de Notariado y Registro",
        "law_name": "Decreto 2723 de 2014",
        "output": "decreto_2723_2014.json",
        "articles": ["21", "22"],
    },
    {
        "key": "estatuto_tributario",
        "index": f"{BASEDOC}/estatuto_tributario.html",
        "source": "Estatuto Tributario - Decreto 624 de 1989",
        "law_name": "Estatuto Tributario (Decreto 624 de 1989)",
        "output": "estatuto_tributario.json",
        "articles": ["630", "839-1", "840"],
        # El expediente cita "839-01"; el artículo oficial es 839-1. Se carga
        # una fila alias con el mismo texto oficial y metadata `alias_of`.
        "title_aliases": {"839-1": ["839-01"]},
        "identifier_aliases": {"839-1": ["839-01"]},
    },
    {
        "key": "codigo_civil",
        "index": f"{BASEDOC}/codigo_civil.html",
        "source": "Código Civil - Ley 57 de 1887",
        "law_name": "Código Civil (Ley 57 de 1887)",
        "output": "codigo_civil_adicional.json",
        "articles": ["769", "1602", "1617", "2432", "2438", "2478", "2499",
                     "2500", "2535"],
    },
    {
        "key": "cgp",
        "index": f"{BASEDOC}/ley_1564_2012.html",
        "source": "Ley 1564 de 2012 - Código General del Proceso",
        "law_name": "Código General del Proceso (Ley 1564 de 2012)",
        "output": "cgp_adicional.json",
        "articles": ["25", "27", "35", "37", "40", "42", "49", "72", "77", "89",
                     "90", "103", "132", "136", "161", "170", "173", "185", "191",
                     "198", "261", "330", "430", "438", "465", "468", "593", "599"],
    },
    {
        "key": "ley_527_1999",
        "index": f"{BASEDOC}/ley_0527_1999.html",
        "source": "Ley 527 de 1999 - Comercio Electrónico y Firma Digital",
        "law_name": "Ley 527 de 1999",
        "output": "ley_527_1999.json",
        "articles": ["7", "12", "20"],
    },
    {
        "key": "ley_2213_2022",
        "index": f"{BASEDOC}/ley_2213_2022.html",
        "source": "Ley 2213 de 2022 - Uso de Tecnologías de la Información",
        "law_name": "Ley 2213 de 2022",
        "output": "ley_2213_2022.json",
        "articles": ["3", "9", "10"],
    },
    {
        "key": "ley_1437_2011",
        "index": f"{BASEDOC}/ley_1437_2011.html",
        "source": "Ley 1437 de 2011 - Código de Procedimiento Administrativo y de lo Contencioso Administrativo (CPACA)",
        "law_name": "Ley 1437 de 2011 (CPACA)",
        "output": "ley_1437_2011.json",
        "articles": ["67", "76", "77", "79", "87"],
    },
    {
        "key": "ley_1394_2010",
        "index": f"{BASEDOC}/ley_1394_2010.html",
        "source": "Ley 1394 de 2010 - Arancel Judicial",
        "law_name": "Ley 1394 de 2010",
        "output": "ley_1394_2010.json",
        "articles": ["6", "7", "8"],
    },
    {
        "key": "ley_962_2005",
        "index": f"{BASEDOC}/ley_0962_2005.html",
        "source": "Ley 962 de 2005 - Racionalización de Trámites",
        "law_name": "Ley 962 de 2005",
        "output": "ley_962_2005.json",
        "articles": ["19"],
    },
    {
        "key": "ley_590_2000",
        "index": f"{BASEDOC}/ley_0590_2000.html",
        "source": "Ley 590 de 2000 - Mipymes",
        "law_name": "Ley 590 de 2000",
        "output": "ley_590_2000.json",
        "articles": ["2", "43"],
    },
    {
        "key": "ley_1581_2012",
        "index": f"{BASEDOC}/ley_1581_2012.html",
        "source": "Ley 1581 de 2012 - Protección de Datos Personales",
        "law_name": "Ley 1581 de 2012",
        "output": "ley_1581_2012.json",
        "articles": ["4", "9"],
    },
    {
        "key": "ley_270_1996",
        "index": f"{BASEDOC}/ley_0270_1996.html",
        "source": "Ley 270 de 1996 - Administración de Justicia",
        "law_name": "Ley 270 de 1996",
        "output": "ley_270_1996.json",
        "articles": ["16"],
    },
    {
        "key": "decreto_ley_019_2012",
        "index": f"{BASEDOC}/decreto_0019_2012.html",
        "source": "Decreto Ley 019 de 2012 - Supresión de Trámites",
        "law_name": "Decreto Ley 019 de 2012",
        "output": "decreto_ley_019_2012.json",
        "articles": ["15", "18"],
    },
    {
        "key": "decreto_525_2009",
        "mode": "single",
        "index": "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=35300",
        "source": "Decreto 525 de 2009 - Mipymes y Aportes Parafiscales",
        "law_name": "Decreto 525 de 2009",
        "output": "decreto_525_2009.json",
        "articles": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "version_date": "2009-02-23",
        "encoding": "utf-8",
        "verify_tls": False,
        "transport": "curl",
        "extraction": "funcionpublica-gestornormativo/v1",
    },
    {
        "key": "decreto_1069_2015",
        "mode": "single",
        "index": "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=74174",
        "source": "Decreto 1069 de 2015 - Decreto Único Reglamentario del Sector Justicia y del Derecho",
        "law_name": "Decreto 1069 de 2015",
        "output": "decreto_1069_2015.json",
        "articles": ["2.2.6.1.2.4.1"],
        "version_date": "2024-04-29",
        "encoding": "utf-8",
        "verify_tls": False,
        "transport": "curl",
        "extraction": "funcionpublica-gestornormativo/v1",
    },
    {
        "key": "decreto_650_1996",
        "mode": "single",
        "index": "https://normograma.dian.gov.co/dian/compilacion/docs/decreto_0650_1996.htm",
        "source": "Decreto 650 de 1996 - Reglamentario de la Ley 223 de 1995",
        "law_name": "Decreto 650 de 1996",
        "output": "decreto_650_1996.json",
        "articles": ["15"],
        "version_date": "1996-04-03",
        "encoding": "utf-8",
        "extraction": "dian-normograma/v1",
    },
    {
        "key": "decreto_2150_1995",
        "index": f"{BASEDOC}/decreto_2150_1995.html",
        "source": "Decreto 2150 de 1995 - Supresión de Trámites y Firma Mecánica",
        "law_name": "Decreto 2150 de 1995",
        "output": "decreto_2150_1995.json",
        "articles": ["12"],
    },
    {
        "key": "decreto_806_2020",
        "index": f"{BASEDOC}/decreto_0806_2020.html",
        "source": "Decreto 806 de 2020 - Notificaciones Electrónicas",
        "law_name": "Decreto 806 de 2020",
        "output": "decreto_806_2020.json",
        "articles": ["8"],
    },
    {
        "key": "decreto_960_1970",
        "index": f"{BASEDOC}/decreto_0960_1970.html",
        "source": "Decreto 960 de 1970 - Estatuto Notarial",
        "law_name": "Decreto 960 de 1970",
        "output": "decreto_960_1970.json",
        "articles": ["68", "80", "81"],
    },
]

NOTE_LINES = {
    "notas del editor", "notas de vigencia", "jurisprudencia vigencia",
    "legislación anterior", "legislacion anterior", "siguiente", "inicio",
    "artículo", "articulo", "resumen de notas de vigencia",
}
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
ARTICLE_START = re.compile(
    r"(?m)^\s*ART[IÍ]CULO\s+(\d+(?:[.\-]\d+)*)(?:[oO°]\.?)?\b", re.I)
UPDATED = re.compile(
    r"Última actualización:\s*(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})",
    re.I,
)


class _TextExtractor(HTMLParser):
    """Extrae texto plano de HTML sin dependencias externas."""

    BLOCK_TAGS = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _get(url: str, verify_tls: bool = True, encoding: str = "ISO-8859-1",
         transport: str = "httpx") -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache = CACHE_DIR / f"{key}.html"
    if cache.exists() and not REFRESH:
        return cache.read_text(encoding=encoding)
    errors: list[str] = []
    if transport == "curl":
        try:
            proc = subprocess.run(
                ["curl", "-fsSL", "--max-time", str(TIMEOUT), url],
                capture_output=True, check=True)
            raw = proc.stdout.decode(encoding)
            cache.write_text(raw, encoding=encoding)
            return raw
        except Exception as exc:  # noqa: BLE001 — fallback a httpx
            errors.append(f"curl: {exc}")
    candidates = [url]
    if url.startswith("http://"):
        candidates.append("https://" + url[len("http://"):])
    for candidate in candidates:
        for attempt in range(4):
            try:
                response = httpx.get(candidate, timeout=TIMEOUT, follow_redirects=True,
                                     verify=verify_tls,
                                     headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                raw = response.content.decode(encoding)
                cache.write_text(raw, encoding=encoding)
                return raw
            except Exception as exc:  # noqa: BLE001 — se reintenta
                errors.append(f"{candidate} (intento {attempt + 1}): {exc}")
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("No se pudo descargar la fuente oficial. " + " | ".join(errors))


def _plain_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    text = "".join(parser.parts)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _source_updated(text: str, url: str) -> str:
    match = UPDATED.search(text)
    if not match:
        raise ValueError(f"No se pudo leer la fecha de actualización en {url}")
    day, month, year = match.groups()
    month_number = MONTHS.get(month.lower())
    if not month_number:
        raise ValueError(f"Mes desconocido en {url}: {month}")
    return datetime(int(year), month_number, int(day)).date().isoformat()


def _article_pages(index_url: str, wanted: list[str], verify_tls: bool = True,
                   encoding: str = "ISO-8859-1", transport: str = "httpx") -> dict[str, str]:
    """Mapea número de artículo -> URL de la página que lo contiene."""
    index_html = _get(index_url, verify_tls=verify_tls, encoding=encoding,
                      transport=transport)
    base = index_url.rsplit("/", 1)[-1].removesuffix(".html")
    pages: dict[str, str] = {}
    for match in re.finditer(r'<option value="([^"]*)">([^<]*)</option>', index_html):
        value, label = html.unescape(match.group(1)), html.unescape(match.group(2)).strip()
        if label not in wanted:
            continue
        page_match = re.search(r"_pr(\d+)", value)
        pages[label] = (f"{BASEDOC}/{base}_pr{page_match.group(1)}.html"
                        if page_match else index_url)
    missing = [number for number in wanted if number not in pages]
    if missing:
        raise ValueError(f"{index_url}: no se encontraron los artículos {missing}")
    return pages


def _extract_article(text: str, number: str) -> str:
    pattern = re.compile(
        rf"(?m)^\s*ART[IÍ]CULO\s+{re.escape(number)}(?:[oO°]\.?)?\b", re.I)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"No se encontró el artículo {number}")
    rest = text[match.start():]
    following = ARTICLE_START.search(rest, match.end() - match.start() + 1)
    block = rest[: following.start()] if following else rest
    # La "Legislación Anterior" es un apéndice editorial con el texto derogado.
    cut = re.search(r"\n\s*Legislaci[oó]n Anterior\s*\n", block, re.I)
    if cut:
        block = block[: cut.start()]
    lines = []
    for line in block.splitlines():
        clean = line.strip()
        if not clean or clean.lower() in NOTE_LINES:
            continue
        lines.append(clean)
    value = " ".join(lines)
    value = re.sub(r"\s+", " ", value).strip()
    # Etiquetas editoriales que pueden quedar en la misma línea del texto.
    value = re.sub(r"\b(?:Notas del Editor|Notas de Vigencia|Jurisprudencia Vigencia)\b", " ", value)
    value = re.sub(r"<Ver[^>]*>", " ", value, flags=re.I)
    value = re.split(r"\bLegislaci[oó]n Anterior\b|\bAnterior\s*\|", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"Disposiciones analizadas por|Derechos de autor reservados|"
                     r"Última actualización:\s*\d", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) < 40:
        raise ValueError(f"Artículo {number}: texto extraído demasiado corto")
    return value


def _title(number: str, law_name: str, aliases: dict[str, list[str]] | None) -> str:
    title = f"ARTÍCULO {number} - {law_name}"
    for alias in (aliases or {}).get(number, []):
        title += f" (ARTÍCULO {alias})"
    return title


def build_source(spec: dict) -> list[dict]:
    verify_tls = bool(spec.get("verify_tls", True))
    encoding = str(spec.get("encoding", "ISO-8859-1"))
    transport = str(spec.get("transport", "httpx"))
    if spec.get("mode") == "single":
        pages = {number: spec["index"] for number in spec["articles"]}
    else:
        pages = _article_pages(spec["index"], spec["articles"],
                               verify_tls=verify_tls, encoding=encoding,
                               transport=transport)
    cache: dict[str, str] = {}
    rows: list[dict] = []
    for number in spec["articles"]:
        url = pages[number]
        if url not in cache:
            cache[url] = _plain_text(_get(url, verify_tls=verify_tls, encoding=encoding,
                                          transport=transport))
        page_text = cache[url]
        version_date = spec.get("version_date") or _source_updated(page_text, url)
        article_text = _extract_article(page_text, number)
        extraction = spec.get("extraction", "secretariasenado-basedoc/v1")
        rows.append({
            "jurisdiction": "co",
            "source": spec["source"],
            "identifier": number,
            "title": _title(number, spec["law_name"], spec.get("title_aliases")),
            "text": article_text,
            "version_date": version_date,
            "official_url": url,
            "checksum": hashlib.sha256(article_text.encode("utf-8")).hexdigest(),
            "metadata": {
                "source_label": spec["law_name"],
                "source_page_updated": version_date,
                "retrieved_at": f"{version_date}T00:00:00+00:00",
                "article_anchor": f"art-{number}",
                "extraction": extraction,
                "builder": "scripts/build_legal_corpus.py",
            },
        })
        # Alias de identificación usados por el expediente (p. ej. 839-01).
        # El texto y el checksum son los del artículo oficial; el alias queda
        # declarado en metadata y en el identificador para que el gate exacto
        # pueda resolverlo sin inventar contenido.
        for alias in (spec.get("identifier_aliases") or {}).get(number, []):
            alias_row = dict(rows[-1])
            alias_row["identifier"] = alias
            alias_row["title"] = (f"ARTÍCULO {alias} - {spec['law_name']} "
                                  f"(ALIAS DEL ARTÍCULO {number})")
            alias_row["metadata"] = {**rows[-1]["metadata"], "alias_of": number}
            rows.append(alias_row)
    return rows


def main() -> None:
    global REFRESH
    parser = argparse.ArgumentParser(description="Construir corpus legal desde fuente oficial")
    parser.add_argument("--dry-run", action="store_true", help="No escribe archivos")
    parser.add_argument("--refresh", action="store_true", help="Ignorar caché local")
    parser.add_argument("--only", help="Claves separadas por coma (ley_1579_2012,cgp,...)")
    args = parser.parse_args()
    REFRESH = args.refresh
    selected = {key.strip() for key in (args.only or "").split(",") if key.strip()}
    specs = [s for s in SOURCES if not selected or s["key"] in selected]
    if not specs:
        raise SystemExit(f"No hay fuentes para --only {args.only}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for spec in specs:
        rows = build_source(spec)
        target = OUT_DIR / spec["output"]
        if not args.dry_run:
            target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        total += len(rows)
        print(f"{'[dry-run] ' if args.dry_run else ''}{spec['key']}: "
              f"{len(rows)} artículos -> {target.relative_to(ROOT)}")
    print(f"Total: {total} artículos")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — CLI con mensaje claro
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
