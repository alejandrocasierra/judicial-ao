#!/usr/bin/env python3
"""Siembra datos sintéticos y cuentas de prueba. Se ejecuta con el rol de la APP
(respeta RLS): cada organización se inserta con su propio app.current_org.

  python -m seeds.seed            (desde apps/api)   |  scripts/db_seed.sh
Rechaza ejecutarse con APP_ENV=production o staging.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "api"))
import envload  # noqa: E402

# Carga el archivo ENV_FILE si existe (host); en contenedor valen las env de compose.
_env_file = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else ROOT / ".env"
if not _env_file.is_absolute():
    _env_file = ROOT / _env_file
if _env_file.exists():
    envload.load(str(_env_file), override=True)

import yaml  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import one, rows, tx  # noqa: E402
from app.security.passwords import hash_password, password_policy_ok  # noqa: E402
from app.services.citations import quote_hash  # noqa: E402
from app.services.storage import original_key, storage  # noqa: E402
from seeds.pdfgen import make_pdf, make_placeholder_mp4  # noqa: E402


def ins(c, table: str, **cols) -> str:
    cols = {k: v for k, v in cols.items() if v is not None}
    for k, v in list(cols.items()):
        if isinstance(v, (dict,)):
            cols[k] = json.dumps(v)
    keys = ", ".join(cols)
    vals = ", ".join(f"CAST(:{k} AS jsonb)" if isinstance(cols[k], str) and k.endswith("_json") else f":{k}" for k in cols)
    return str(one(c, f"INSERT INTO {table} ({keys}) VALUES ({vals}) RETURNING *", **cols).get("id", ""))


def seed() -> list[dict]:
    s = get_settings()
    if s.APP_ENV in ("production", "staging"):
        raise SystemExit("[seed] refused: APP_ENV is production/staging")
    domain, password = envload.require("SEED_EMAIL_DOMAIN", "SEED_DEFAULT_PASSWORD")
    if not password_policy_ok(password):
        raise SystemExit("[seed] SEED_DEFAULT_PASSWORD does not meet the password policy")
    data = yaml.safe_load(s.path(os.environ["SEED_DATA_FILE"]).read_text(encoding="utf-8"))
    # Un hash por usuario (sal única): nunca reutilizar el mismo hash aunque la contraseña semilla coincida
    accounts: list[dict] = []

    for org in data["organizations"]:
        first_email = f"{org['users'][0]['local_part']}@{domain}"
        with tx(None) as c:
            if one(c, "SELECT id FROM auth_find_user(:e)", e=first_email):
                print(f"[seed] organization '{org['slug']}' already seeded — skipping")
                continue
        org_id = str(uuid.uuid4())
        with tx(org_id) as c:
            c.execute(text("INSERT INTO organizations (id, name, slug, default_locale) VALUES (:i,:n,:s,:l)"),
                      {"i": org_id, "n": org["name"], "s": org["slug"], "l": org["default_locale"]})
            users = {}
            for u in org["users"]:
                email = f"{u['local_part']}@{domain}"
                users[u["key"]] = ins(c, "users", organization_id=org_id, email=email, full_name=u["full_name"],
                                      password_hash=hash_password(password), org_role=u["org_role"], locale=u["locale"],
                                      is_active=u.get("is_active", True))
                accounts.append({"org": org["slug"], "email": email, "role": u["org_role"],
                                 "active": u.get("is_active", True)})
            for cs in org.get("cases", []):
                _seed_case(c, org_id, users, cs)
        print(f"[seed] organization '{org['slug']}' seeded")
    _seed_bootstrap_admin(accounts)
    _seed_builtin_agents()
    return accounts


def _seed_builtin_agents() -> None:
    """Siembra las skills jurídicas de sistema y las enlaza al agente de sistema."""
    from app.services import builtin_agents  # import perezoso (usa app.*)

    with tx(None) as c:
        orgs = rows(c, "SELECT id FROM ops_list_organizations()")
    for org in orgs:
        org_id = str(org["id"])
        with tx(org_id) as c:
            skills = builtin_agents.ensure_builtin_skills(c, org_id, None)
            existing = one(c, "SELECT id FROM agents WHERE organization_id = :o AND kind = 'system'", o=org_id)
            if existing:
                c.execute(text("UPDATE agents SET skills = :sk, updated_at = now() WHERE id = :i"),
                          {"sk": list(skills.values()), "i": str(existing["id"])})
            else:
                builtin_agents.ensure_builtin_agents(c, org_id, None)
    print(f"[seed] agente de sistema y {len(builtin_agents.BUILTIN_SKILLS)} skills listos")


def _seed_bootstrap_admin(accounts: list[dict]) -> None:
    """Crea/actualiza un admin de acceso rápido (BOOTSTRAP_ADMIN_*), opcional.

    No afecta a las pruebas: .env.test no define estas variables. El admin se
    asocia a la primera organización registrada y es idempotente por email.
    """
    email = (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or ""
    full_name = (os.environ.get("BOOTSTRAP_ADMIN_NAME") or "Administrador").strip()
    if not email or not password:
        return
    if not password_policy_ok(password):
        print("[seed] BOOTSTRAP_ADMIN_PASSWORD no cumple la política — omitido")
        return
    with tx(None) as c:
        # Prefiere la organización 'alfa' (donde vive el expediente piloto); si no, la primera.
        org = one(c, "SELECT id FROM ops_list_organizations() WHERE name ILIKE '%alfa%' LIMIT 1")
        if not org:
            org = one(c, "SELECT id FROM ops_list_organizations() ORDER BY name LIMIT 1")
    if not org:
        return
    org_id = str(org["id"])
    with tx(org_id) as c:
        existing = one(c, "SELECT id FROM users WHERE email = :e", e=email)
        if existing:
            c.execute(text("""UPDATE users SET password_hash = :h, full_name = :n, org_role = 'ORG_ADMIN',
                              is_active = true, failed_login_attempts = 0, locked_until = NULL WHERE id = :u"""),
                      {"h": hash_password(password), "n": full_name, "u": str(existing["id"])})
        else:
            # RIESGO: el admin puede existir ya en OTRA organización (p. ej.
            # scripts/seed_admin.sh lo crea en la primera org alfabética). RLS de
            # ESTA org no lo deja ver, pero `users.email` es UNIQUE global, así que
            # un INSERT simple revienta con users_email_key (UniqueViolation) y el
            # paso de semillas muere con traceback. ON CONFLICT lo convierte en un
            # no-op atómico sin perder el UPDATE de arriba cuando sí se ve.
            res = c.execute(text("""INSERT INTO users (organization_id, email, full_name, password_hash, org_role, locale)
                                    VALUES (:o, :e, :n, :h, 'ORG_ADMIN', 'es')
                                    ON CONFLICT (email) DO NOTHING"""),
                            {"o": org_id, "e": email, "n": full_name, "h": hash_password(password)})
            if res.rowcount == 0:
                print(f"[seed] bootstrap admin {email} ya existe en otra organización — no se duplica")
                return
    accounts.append({"org": "alfa", "email": email, "role": "ORG_ADMIN", "active": True})
    print(f"[seed] bootstrap admin listo: {email}")


def _seed_case(c, org_id: str, users: dict, cs: dict) -> None:
    s = get_settings()
    first_member = next(iter(cs["members"]))
    case_id = ins(c, "cases", organization_id=org_id, jurisdiction=cs["jurisdiction"], case_number=cs["case_number"],
                  title=cs["title"], court=cs.get("court"), language=cs["language"], status=cs.get("status", "CREATED"),
                  max_processing_cost=s.CASE_MAX_PROCESSING_COST, max_llm_tokens=s.CASE_MAX_LLM_TOKENS,
                  max_media_hours=s.CASE_MAX_MEDIA_HOURS, created_by=users[first_member])
    for ukey, role in cs["members"].items():
        c.execute(text("INSERT INTO case_members (case_id, user_id, organization_id, case_role) VALUES (:c,:u,:o,:r)"),
                  {"c": case_id, "u": users[ukey], "o": org_id, "r": role})
    parties = {p["key"]: ins(c, "parties", organization_id=org_id, case_id=case_id, name=p["name"],
                             normalized_name=p["name"].lower(), role=p["role"], entity_type=p["entity_type"],
                             aliases=p.get("aliases", [])) for p in cs.get("parties", [])}
    docs, pages = {}, {}
    for d in cs.get("documents", []):
        blob = make_pdf([pg["text"] for pg in d["pages"]])
        sha = hashlib.sha256(blob).hexdigest()
        uri = storage().put(original_key(case_id, sha, "document"), blob)
        docs[d["key"]] = ins(c, "documents", organization_id=org_id, case_id=case_id, storage_uri=uri, sha256=sha,
                             size_bytes=len(blob), mime_type="application/pdf", filename=d["filename"],
                             document_type=d["document_type"], document_date=d.get("document_date"), page_count=len(d["pages"]),
                             folio_start=d.get("folio_start"), language=d.get("language"), processing_status="EXTRACTED",
                             parser_version=s.PIPELINE_VERSION, uploaded_by=users[first_member])
        for n, pg in enumerate(d["pages"], 1):
            folio = str(int(d["folio_start"]) + n - 1) if d.get("folio_start") else None
            ins(c, "document_pages", organization_id=org_id, document_id=docs[d["key"]], page_number=n, folio=folio,
                text=pg["text"], ocr_confidence=pg["ocr_confidence"],
                needs_review=pg["ocr_confidence"] < s.OCR_CONFIDENCE_THRESHOLD)
            pages[(d["key"], n)] = {"text": pg["text"], "folio": folio}
    media, segs = {}, {}
    for m in cs.get("media", []):
        blob = make_placeholder_mp4(f"{case_id}:{m['key']}")
        sha = hashlib.sha256(blob).hexdigest()
        uri = storage().put(original_key(case_id, sha, "media"), blob)
        media[m["key"]] = ins(c, "media", organization_id=org_id, case_id=case_id, storage_uri=uri, sha256=sha,
                              size_bytes=len(blob), mime_type="video/mp4", filename=m["filename"], title=m.get("title"),
                              media_type="video", duration_ms=m["duration_ms"], processing_status="TRANSCRIBED",
                              uploaded_by=users[first_member])
        spk = {sp["key"]: ins(c, "speakers", organization_id=org_id, case_id=case_id, label=sp["label"],
                              speaker_role=sp["speaker_role"], resolved_party_id=parties.get(sp.get("party")),
                              resolution_status=sp["resolution_status"], resolution_source=sp.get("resolution_source"),
                              confidence=sp["confidence"]) for sp in m.get("speakers", [])}
        for i, sg in enumerate(m.get("segments", [])):
            sid = ins(c, "transcript_segments", organization_id=org_id, media_id=media[m["key"]], speaker_id=spk[sg["speaker"]],
                      start_ms=sg["start_ms"], end_ms=sg["end_ms"], text=sg["text"], confidence=sg["confidence"],
                      needs_review=sg["confidence"] < s.ASR_CONFIDENCE_THRESHOLD, language=cs["language"])
            segs[(m["key"], i)] = {"id": sid, **sg}

    def cite(target_type: str, target_id: str, src: dict) -> None:
        if "document" in src:
            pg = pages[(src["document"], src["page"])]
            extra = {}
            if src.get("quote"):
                start = pg["text"].find(src["quote"])
                if start < 0:
                    raise SystemExit(f"[seed] quote not found in {src['document']} p{src['page']}")
                extra = {"char_start": start, "char_end": start + len(src["quote"]), "quote_hash": quote_hash(src["quote"])}
            ins(c, "citations", organization_id=org_id, case_id=case_id, target_type=target_type, target_id=target_id,
                source_type="document_page", document_id=docs[src["document"]], page_number=src["page"], folio=pg["folio"], **extra)
        else:
            sg = segs[(src["media"], src["segment"])]
            ins(c, "citations", organization_id=org_id, case_id=case_id, target_type=target_type, target_id=target_id,
                source_type="transcript_segment", media_id=media[src["media"]], segment_id=sg["id"],
                start_ms=sg["start_ms"], end_ms=sg["end_ms"])

    claims = {}
    for cl in cs.get("claims", []):
        claims[cl["key"]] = ins(c, "claims", organization_id=org_id, case_id=case_id, text=cl["text"], claim_type=cl["claim_type"],
                                claimant_party_id=parties.get(cl.get("claimant")), confidence=cl["confidence"],
                                original_ai_output=json.dumps({"text": cl["text"], "claim_type": cl["claim_type"]}))
        cite("claim", claims[cl["key"]], cl["source"])
    facts = {}
    for f in cs.get("facts", []):
        facts[f["key"]] = ins(c, "facts", organization_id=org_id, case_id=case_id, proposition=f["proposition"], status=f["status"])
        for link in f.get("claims", []):
            c.execute(text("INSERT INTO fact_claims (organization_id, fact_id, claim_id, stance) VALUES (:o,:f,:c,:s)"),
                      {"o": org_id, "f": facts[f["key"]], "c": claims[link["claim"]], "s": link["stance"]})
    for e in cs.get("evidence", []):
        eid = ins(c, "evidence", organization_id=org_id, case_id=case_id, evidence_type=e["evidence_type"], description=e["description"],
                  source_document_id=docs.get(e.get("document")), source_media_id=media.get(e.get("media")))
        for link in e.get("links", []):
            ins(c, "evidence_links", organization_id=org_id, evidence_id=eid, fact_id=facts[link["fact"]], stance=link["stance"])
    for ev in cs.get("events", []):
        evid = ins(c, "events", organization_id=org_id, case_id=case_id, event_type=ev["event_type"], event_date=ev["date"],
                   description=ev["description"], timeline_confidence=ev["timeline_confidence"])
        cite("event", evid, ev["source"])
    for co in cs.get("contradictions", []):
        ins(c, "contradictions", organization_id=org_id, case_id=case_id, claim_a_id=claims[co["claim_a"]],
            claim_b_id=claims[co["claim_b"]], contradiction_type=co["contradiction_type"], description=co["description"],
            severity=co["severity"])


def main() -> None:
    accounts = seed()
    if accounts:
        print("\n[seed] Cuentas de prueba / Test accounts (password = SEED_DEFAULT_PASSWORD en tu .env):")
        print(f"{'org':<6} {'email':<34} {'role':<13} active")
        for a in accounts:
            print(f"{a['org']:<6} {a['email']:<34} {a['role']:<13} {a['active']}")


if __name__ == "__main__":
    main()
