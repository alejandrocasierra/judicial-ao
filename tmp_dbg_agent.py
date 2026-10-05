import os
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "apps/api")
import envload

envload.load(".env", override=True)

import psycopg
from app.core.db import tx
from app.providers.llm import get_llm_for_model
from app.services import agent, agent_tools, query_hints

conn = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
                       dbname=os.environ["POSTGRES_DB"], user=os.environ["DB_OWNER_USER"],
                       password=os.environ["DB_OWNER_PASSWORD"], autocommit=True)
cur = conn.cursor()
cur.execute("SELECT id::text, organization_id::text FROM users WHERE is_active AND org_role='ORG_ADMIN' LIMIT 1")
uid, org = cur.fetchone()
cur.execute("SELECT case_id::text FROM media WHERE filename ILIKE '%0077%' LIMIT 1")
case = cur.fetchone()[0]
cur.execute("SELECT id::text FROM ai_models ORDER BY is_default DESC, provider, model_name LIMIT 1")
mid = cur.fetchone()[0]

q = "¿En qué minuto habló la doctora Paola?"
orig = agent_tools.execute


def logged(_conn, cid, tool, args):
    items = orig(_conn, cid, tool, args)
    print("  TOOL:", tool, str(args)[:90], "->", len(items), sorted({i.get('source_type') for i in items}))
    return items


agent_tools.execute = logged
llm = get_llm_for_model(mid, org, uid)
with tx(org, uid) as c:
    res = agent.run_agent_query(c, case, q, "es", llm=llm, org_id=org, actor_id=uid,
                                hints=[query_hints.hint(q)])
print("evidence:", res.get("evidence_count"))
print("claims:", [cl["text"][:80] for cl in res.get("claims", [])])
print("unsupported:", [(u.get("reason"), u["text"][:70]) for u in res.get("unsupported_claims", [])][:6])
print("raw_text:", (res.get("raw_text") or "")[:500])
conn.close()
