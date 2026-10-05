# Judicial AI Platform

**ES** · Plataforma *evidence-first* que convierte expedientes judiciales multimodales en un conocimiento
verificable. Cada afirmación queda anclada a documento + página o a media + timestamp. · **EN** ·
Evidence-first platform that turns multimodal court case files into verifiable knowledge, with every
statement anchored to document + page or media + timestamp.

---

## Español

### Inicio rápido
| Entorno | Comando |
|---|---|
| Claude Code en la web | Automático (hook `SessionStart`). Si falla, ejecuta `bash scripts/setup_cloud_session.sh`. |
| Linux con PostgreSQL 16 y pgvector | `bash scripts/bootstrap.sh` |
| Linux con Docker | `python3 scripts/gen_env.py && bash scripts/bootstrap.sh --docker` |

Después:
```bash
bash scripts/dev_server.sh          # API en http://127.0.0.1:8000 (API_HOST/API_PORT)
bash scripts/run_tests.sh           # 383 casos: unit · integration · security · behavior · static
```

### Configuración
Toda la configuración va en variables de entorno, sin valores quemados. La referencia completa está en
`.env.example`, con comentarios. `scripts/gen_env.py` genera `.env` y `.env.test` con secretos aleatorios.
Nunca versiones esos archivos.

Para usar un modelo real, define `LLM_PROVIDER=anthropic`, `LLM_MODEL` y `LLM_API_KEY` en `.env`.

### Cuentas de prueba
Hay 10 cuentas sintéticas en dos organizaciones (español e inglés). Todas usan el dominio `example.test`
y la contraseña es `SEED_DEFAULT_PASSWORD` de tu `.env`. Consulta `docs/TEST_ACCOUNTS.md`.

### Documentación
- `CLAUDE.md`: reglas del repo y guía para Claude Code.
- `docs/SSD_ADDENDUM.md`: análisis de brechas y decisiones. Incluye modelo de datos, RBAC, API, estados y modelo de amenazas.
- `docs/TEST_PLAN.md`: estrategia de pruebas.
- `docs/TEST_CATALOG.md`: cada prueba (se genera automáticamente).
- `docs/BACKLOG.md`: qué está hecho y qué sigue.

---

## English

### Quick start
| Environment | Command |
|---|---|
| Claude Code on the web | Automatic (`SessionStart` hook). If it fails, run `bash scripts/setup_cloud_session.sh`. |
| Linux with PostgreSQL 16 + pgvector | `bash scripts/bootstrap.sh` |
| Linux with Docker | `python3 scripts/gen_env.py && bash scripts/bootstrap.sh --docker` |

Then run `bash scripts/dev_server.sh` and `bash scripts/run_tests.sh`.

### Configuration
All configuration comes from environment variables; nothing is hard-coded. `.env.example` is the annotated
reference, and `scripts/gen_env.py` creates `.env` and `.env.test` with random secrets. Never commit them.

### Language
The API speaks Spanish and English. Pick one with `Accept-Language`, or leave it to the user's locale.
Errors keep a stable `code` and a translated `message`.

### Test accounts
There are 10 synthetic accounts across two organizations, all on `example.test`. The password is
`SEED_DEFAULT_PASSWORD` from your `.env`. See `docs/TEST_ACCOUNTS.md`.
