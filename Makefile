# Atajos (todos delegan en scripts/). Ejecuta `make help`.
.PHONY: help setup bootstrap dev test test-fast test-security test-behavior reset migrate seed lint
help:            ## Muestra esta ayuda
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'
setup:           ## Entorno efímero desde cero (Claude Code web / CI)
	bash scripts/setup_cloud_session.sh
bootstrap:       ## Máquina local persistente (añade ARGS=--docker para usar docker compose)
	bash scripts/bootstrap.sh $(ARGS)
dev:             ## API en modo recarga
	bash scripts/dev_server.sh
test:            ## Toda la batería (resetea la BD de pruebas)
	bash scripts/run_tests.sh all
test-fast:       ## Unitarias + estáticas (sin BD)
	bash scripts/run_tests.sh fast
test-security:   ## Sólo seguridad
	bash scripts/run_tests.sh security
test-behavior:   ## Sólo DEBE / NO DEBE
	bash scripts/run_tests.sh behavior
reset:           ## Recrea la BD de desarrollo con semillas
	bash scripts/reset_db.sh
migrate:         ## Aplica migraciones
	bash scripts/db_migrate.sh up
seed:            ## Siembra datos sintéticos
	bash scripts/db_seed.sh
lint:            ## Ruff
	ruff check apps tests scripts
