"""Servidor MCP del Chat IA (Fase 2): expone las case_tools de la Fase 1 como
tools MCP estándar sobre streamable HTTP, con el mismo JWT de la plataforma,
RLS por llamada y allowlist de tools por rol vía RBAC. Cero duplicación:
toda la lógica vive en app/services/case_tools/."""
