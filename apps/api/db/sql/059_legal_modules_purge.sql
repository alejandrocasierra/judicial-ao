-- 059 - Permisos de purga para las tablas de los modulos legales.
-- La API solo elimina estas filas desde purge_case (transaccion con
-- app.allow_evidence_purge='on'); el resto del tiempo son append-only.

GRANT DELETE ON answer_audits, answer_audit_claims, answer_audit_citations,
  answer_audit_findings, second_opinion_runs, second_opinion_claims,
  second_opinion_findings, fact_revisions, legal_rule_citations TO "{{DB_APP_USER}}";
GRANT DELETE ON fact_positions TO "{{DB_APP_USER}}";
