"""Punto de entrada: python -m mcp_server (servicio `mcp` de docker compose)."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Permite ejecutar sin PYTHONPATH previo (apps/api al lado de apps/mcp_server).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.core.config import get_settings  # noqa: E402

log = logging.getLogger("mcp_server")


def main() -> None:
    s = get_settings()
    if not s.MCP_SERVER_ENABLED:
        log.info("MCP_SERVER_ENABLED=false; el servidor MCP no arranca")
        return
    import uvicorn
    from mcp_server.server import app
    uvicorn.run(app, host=s.MCP_SERVER_HOST, port=s.MCP_SERVER_PORT,
                log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
