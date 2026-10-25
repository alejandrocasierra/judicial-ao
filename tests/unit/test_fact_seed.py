"""UT-FSD — siembra de hechos con cita primaria resuelta (contrato F2 · R3)."""
import pytest

from app.services import fact_seed

pytestmark = pytest.mark.unit

CASE = "case-1"


class _Rows:
    """Stub de `fact_seed.rows`.

    `app.core.db.rows` devuelve `dict`, así que el stub devuelve dicts y despacha
    según el fragmento de SQL.
    """

    def __init__(self, *, facts=None, citations=None, positions=None, sources=None,
                 chunks=None, segments=None):
        self.data = {
            "WITH case_facts": facts or [],
            "fact_positions": positions or [],
            "c.target_type = 'fact'": citations or [],
            "FROM evidence_links el": sources or [],
            "FROM chunks c": chunks or [],
            "FROM transcript_segments s": segments or [],
        }
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _conn, sql, **params):
        self.calls.append((sql, params))
        for needle, value in self.data.items():
            if needle in sql:
                data = value(params) if callable(value) else value
                return [dict(row) for row in data]
        return []

    def count(self, needle: str) -> int:
        return sum(1 for sql, _ in self.calls if needle in sql)


@pytest.fixture
def install(monkeypatch):
    def _install(**kwargs) -> _Rows:
        stub = _Rows(**kwargs)
        monkeypatch.setattr(fact_seed, "rows", stub)
        return stub
    return _install


def _fact(ident: str, proposition: str, status: str = "EVIDENCE_SUPPORTED",
          ordinal: int = 1, rank: float = 0.05) -> dict:
    return {"id": ident, "proposition": proposition, "proof_status": status,
            "fact_code": None, "ordinal": ordinal, "rank": rank}


PAGE = {
    "document_id": "doc-1", "page_number": 10, "folio": "10",
    "filename": "0015 Contestacion.pdf",
    "text": "### CAPITALIZACION DE INTERESES. El demandante cobró intereses de usura.", "rank": 3.1,
}


def test_fsd_01_or_query_uses_quoted_lexemes():
    assert fact_seed.or_query(["intereses", "usura"]) == "'intereses' | 'usura'"


def test_fsd_02_or_query_escapes_quotes_backslashes_and_dedupes():
    query = fact_seed.or_query(["o'neil", "a\\b", "USURA", "usura", ""])
    assert query == "'o neil' | 'a b' | 'usura'"
    assert query.count("'") == 6


def test_fsd_03_requires_two_content_terms(install):
    stub = install()
    assert fact_seed.for_question(object(), CASE, "¿usura?") == []
    assert stub.calls == []


def test_fsd_04_fact_without_resolvable_citation_is_omitted(install):
    """A3: sin cita resoluble el hecho se descarta; nunca se inventa una página."""
    install(facts=[_fact("f1", "El demandante cobró intereses de usura.")])
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert items == []


def test_fsd_05_single_fact_term_in_source_is_rejected(install):
    install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.")],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[dict(PAGE, text="El demandante compareció al juzgado.", rank=0.2)],
    )
    assert fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?") == []


def test_fsd_06_builds_document_page_item_with_fact_metadata(install):
    install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.", ordinal=7)],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[PAGE],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert len(items) == 1
    item = items[0]
    assert item["handle"] == "F1"
    assert item["source_type"] == "document_page"
    assert (item["document_id"], item["page_number"]) == ("doc-1", 10)
    assert item["seed"] == "fact"
    assert item["fact_id"] == "f1"
    assert item["fact_number"] == "7"
    assert item["proof_status"] == "EVIDENCE_SUPPORTED"
    assert item["fact_label"] == "[Hecho #7 · EVIDENCE_SUPPORTED]"
    assert item["fact_text"].startswith("El demandante cobró")
    assert item["filename"] == "0015 Contestacion.pdf"


def test_fsd_07_page_text_is_capped(install):
    long_page = dict(PAGE, text="cobró intereses de usura. " * 400)
    install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.")],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[long_page],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert len(items[0]["text"]) <= fact_seed.PAGE_CHARS


def test_fsd_08_builds_transcript_segment_item(install):
    install(
        facts=[_fact("f1", "El demandado negó la capitalización de intereses.", ordinal=3)],
        sources=[{"document_id": None, "media_id": "media-1", "supports": 1}],
        segments=[{"segment_id": "seg-1", "media_id": "media-1", "start_ms": 95000,
                   "end_ms": 100000,
                   "text": "yo niego la capitalización de intereses que se cobró",
                   "filename": "GrabacionAudiencia13Dic2024.mp4", "speaker": "Rojas Melo",
                   "rank": 2.0}],
    )
    items = fact_seed.for_question(object(), CASE,
                                   "¿qué dijo cada parte sobre la capitalización de intereses?")
    assert len(items) == 1
    item = items[0]
    assert item["source_type"] == "transcript_segment"
    assert item["seed"] == "fact"
    assert item["segment_id"] == "seg-1"
    assert item["start_mmss"] == "01:35"
    assert item["speaker"] == "Rojas Melo"
    assert item["fact_label"].startswith("[Hecho #3")


def test_fsd_09_dedupes_the_same_page(install):
    install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.", ordinal=1),
               _fact("f2", "El demandante cobró intereses de usura y capitalización.", ordinal=2)],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[PAGE],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert len(items) == 1


def test_fsd_10_prefers_proven_facts_after_ranking(install):
    install(
        facts=[_fact("alegado", "El demandante cobró intereses de usura.", status="ALLEGED", ordinal=1),
               _fact("probado", "La capitalización de intereses de usura fue cobrada.",
                     status="JUDICIALLY_PROVEN", ordinal=2)],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[PAGE],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert [it["proof_status"] for it in items] == ["JUDICIALLY_PROVEN"]


def test_fsd_11_respects_the_limit(install):
    pages = iter([dict(PAGE, page_number=10), dict(PAGE, page_number=11),
                  dict(PAGE, page_number=12)])
    install(
        facts=[_fact(f"f{i}", "El demandante cobró intereses de usura.", ordinal=i)
               for i in range(1, 4)],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=lambda _params: [next(pages)],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?", limit=2)
    assert [it["page_number"] for it in items] == [10, 11]


def test_fsd_11b_dedupe_collapses_two_facts_on_the_same_page(install):
    install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.", ordinal=1),
               _fact("f2", "El demandante cobró intereses de usura y capitalización.", ordinal=2)],
        sources=[{"document_id": "doc-1", "media_id": None, "supports": 1}],
        chunks=[PAGE],
    )
    assert len(fact_seed.for_question(object(), CASE,
                                      "¿se cobraron intereses de usura?", limit=6)) == 1


def test_fsd_12_uses_the_registered_fact_citation_first(install):
    stub = install(
        facts=[_fact("f1", "El demandante cobró intereses de usura.")],
        citations=[{"source_type": "document_page", "document_id": "doc-9", "page_number": 4,
                    "folio": "4", "media_id": None, "segment_id": None, "start_ms": None,
                    "end_ms": None, "text": "cobró intereses de usura", "filename": "auto.pdf",
                    "speaker": None}],
    )
    items = fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?")
    assert items[0]["document_id"] == "doc-9"
    assert stub.count("FROM chunks c") == 0


def test_fsd_13_zero_limit_returns_empty(install):
    stub = install()
    assert fact_seed.for_question(object(), CASE, "¿se cobraron intereses de usura?", limit=0) == []
    assert stub.calls == []


def test_fsd_14_dated_media_prefers_the_hearing_filename(monkeypatch):
    """Un hecho del 13Dic2024 se ancla en la grabación, no solo en la providencia."""
    seen: list[dict] = []

    def fake_rows(_conn, sql, **params):
        if "FROM media" in sql:
            seen.append(params)
            return [{"id": "media-0065"}]
        return []

    monkeypatch.setattr(fact_seed, "rows", fake_rows)
    media = fact_seed._dated_media(None, CASE, {
        "proposition": "Rojas Melo rindió declaración el 13 de diciembre de 2024."})
    assert media == ["media-0065"]
    assert seen and seen[0]["q"] == "%13Dic2024%"


def test_fsd_15_legal_anchor_returns_the_886_segment(monkeypatch):
    def fake_rows(_conn, sql, **params):
        if "FROM transcript_segments ts" in sql:
            return [{"segment_id": "s1", "media_id": "m1", "start_ms": 8429880,
                     "end_ms": 8435000, "text": "La ley autoriza mediante el artículo 886 "
                     "del Código de Comercio que sobre los intereses capitalizados se causen "
                     "nuevos intereses.", "filename": "0083Grabacion25Junio2025Parte1.mp4",
                     "speaker": "Alba Lucy Cock Álvarez"}]
        return []

    monkeypatch.setattr(fact_seed, "rows", fake_rows)
    item = fact_seed._legal_anchor(None, CASE, 1)
    assert item is not None
    assert item["source_type"] == "transcript_segment"
    assert item["seed"] == "legal"
    assert "886" in item["text"]
    assert item["start_mmss"] == "2:20:29"


def test_fsd_16_inventory_item_prefers_the_page_with_the_count(monkeypatch):
    def fake_rows(_conn, sql, **params):
        if "FROM document_pages dp" in sql:
            return [{"document_id": "doc-1", "page_number": 2, "folio": "2",
                     "filename": "0095Providencia.pdf",
                     "text": "Exactamente 15 pagarés. 15 pagarés le firmó en el 2010."}]
        return []

    monkeypatch.setattr(fact_seed, "rows", fake_rows)
    items = fact_seed._inventory_items(None, CASE, "¿Cuántos pagarés hay?", 1)
    assert items
    item = items[0]
    assert item["seed"] == "inventory"
    assert "15 pagarés" in item["text"]
    assert item["document_id"] == "doc-1" and item["page_number"] == 2


def test_fsd_17_misspelled_interrogatorios_still_reads_the_hearing(monkeypatch):
    """«interregatorios» (typo del usuario) debe activar la búsqueda en el audio."""
    def fake_rows(_conn, sql, **params):
        if "FROM document_pages dp" in sql:
            return [{"document_id": "doc-65", "page_number": 9, "folio": "9",
                     "filename": "0065Acta.pdf",
                     "text": "En la audiencia del 13 de diciembre de 2024 se practicó "
                             "interrogatorio de parte."}]
        if "FROM media" in sql:
            return [{"id": "media-0065"}]
        if "FROM transcript_segments s" in sql:
            return [{"segment_id": "s-1", "media_id": "media-0065", "start_ms": 2228000,
                     "end_ms": 2234000,
                     "text": "Son pagarés sobre intereses vencidos de muchas obligaciones "
                             "que estoy cobrando vía ejecutiva.",
                     "filename": "0065Grabacion.mp4", "speaker": "Jorge Rojas"}]
        return []

    monkeypatch.setattr(fact_seed, "rows", fake_rows)
    items = fact_seed._declaration_items(
        None, CASE,
        "revisa la documentación, interregatorios y hechos sobre intereses de usura", 1)
    assert items
    assert items[0]["source_type"] == "transcript_segment"
    assert "interregatorios" not in fact_seed._substantive_terms(
        "revisa los interregatorios sobre intereses de usura")
