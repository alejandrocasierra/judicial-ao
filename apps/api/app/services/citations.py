"""Citation engine (SSD §19, §133): una cita es válida sólo si la fuente existe y coincide."""
from __future__ import annotations

import hashlib

from sqlalchemy.engine import Connection

from app.core.db import one


def quote_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate(conn: Connection, c: dict) -> tuple[bool, list[str], dict]:
    reasons: list[str] = []
    source: dict = {}
    if c["source_type"] == "document_page":
        page = one(conn, "SELECT p.text, p.folio, d.filename, d.case_id FROM document_pages p JOIN documents d ON d.id = p.document_id "
                         "WHERE p.document_id = :d AND p.page_number = :n", d=str(c["document_id"]), n=c["page_number"])
        if not page:
            return False, ["page_not_found"], source
        if str(page["case_id"]) != str(c["case_id"]):
            reasons.append("case_mismatch")
        source = {"document_id": str(c["document_id"]), "filename": page["filename"], "page": c["page_number"],
                  "folio": page["folio"]}
        if c.get("char_start") is not None:
            if c["char_end"] > len(page["text"]):
                reasons.append("offset_out_of_range")
            else:
                excerpt = page["text"][c["char_start"]:c["char_end"]]
                source["excerpt"] = excerpt
                if c.get("quote_hash") and quote_hash(excerpt) != c["quote_hash"].strip():
                    reasons.append("quote_hash_mismatch")
    else:
        seg = one(conn, "SELECT s.media_id, s.start_ms, s.end_ms, s.text, sp.label AS speaker, sp.resolution_status, m.case_id "
                        "FROM transcript_segments s JOIN media m ON m.id = s.media_id LEFT JOIN speakers sp ON sp.id = s.speaker_id "
                        "WHERE s.id = :id", id=str(c["segment_id"]))
        if not seg:
            return False, ["segment_not_found"], source
        if str(seg["media_id"]) != str(c["media_id"]):
            reasons.append("media_mismatch")
        if str(seg["case_id"]) != str(c["case_id"]):
            reasons.append("case_mismatch")
        if c["start_ms"] < seg["start_ms"] or c["end_ms"] > seg["end_ms"]:
            reasons.append("timestamp_out_of_segment")
        source = {"media_id": str(c["media_id"]), "segment_id": str(c["segment_id"]), "start_ms": c["start_ms"],
                  "end_ms": c["end_ms"], "speaker": seg["speaker"], "speaker_resolution": seg["resolution_status"],
                  "excerpt": seg["text"]}
    return not reasons, reasons, source
