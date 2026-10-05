# Atajos de despliegue (modo COMPARTIDO). Ver docs/DEPLOY_GCP.md.
# Uso: make deploy   |   make help   |   make verify
#
# Variables:
#   ENV        archivo de entorno de la instancia (default .env.advisorlegal = site)
#   EXPORT     carpeta exportada del expediente (para `make import`)
#   INSTANCES  instancias a operar (default: site develop quality)
#   CASE/ORG/USER/GEMINI_KEY   para `make reindex` y `make models`

SHELL := /bin/bash
ENV ?= .env.advisorlegal
EXPORT ?=
INSTANCES ?=
CASE ?=
ORG ?=
USER ?=
GEMINI_KEY ?=

.PHONY: help deploy verify verify-local gemini import migrate admin models reindex infra proxy build up down ps logs

help:
	@echo "Targets (ENV=$(ENV)):"
	@echo "  make deploy                     despliegue completo (modo compartido)"
	@echo "  make verify                     health + TLS de los 3 dominios"
	@echo "  make verify-local               health por puertos en el host"
	@echo "  make gemini  [ENV=...]          prueba de humo de Gemini"
	@echo "  make import  EXPORT=casos/<uuid>   carga el expediente en las 3"
	@echo "  make migrate                    migraciones + semillas multi-BD"
	@echo "  make admin   ENV=...            org + admin (seed_admin)"
	@echo "  make models  ORG=<uuid> USER=<uuid> GEMINI_KEY=<key>"
	@echo "  make reindex CASE=<uuid> ORG=<uuid> USER=<uuid>"
	@echo "  make infra | proxy | build | up | down | ps | logs"

deploy:
	bash scripts/deploy_all.sh $(if $(EXPORT),--export-dir $(EXPORT)) $(if $(INSTANCES),--instances "$(INSTANCES)")

verify:
	bash scripts/verify_deploy.sh

verify-local:
	bash scripts/verify_deploy.sh --local

gemini:
	bash scripts/check_gemini.sh $(ENV)

import:
	@test -n "$(EXPORT)" || { echo "Falta EXPORT=casos/<uuid>"; exit 2; }
	bash scripts/import_case_all.sh --export-dir "$(EXPORT)" $(if $(INSTANCES),--instances "$(INSTANCES)")

migrate:
	DOCKER=1 bash scripts/migrate_seeds.sh .env.develop .env.quality
	DOCKER=1 bash scripts/migrate_seeds.sh --no-seed .env.advisorlegal

admin:
	ENV_FILE="$(ENV)" bash scripts/seed_admin.sh

models:
	@test -n "$(ORG)" && test -n "$(USER)" && test -n "$(GEMINI_KEY)" || { echo "Faltan ORG= USER= GEMINI_KEY="; exit 2; }
	docker compose --env-file "$(ENV)" exec -T api python /srv/scripts/seed_ai_models.py --org-id "$(ORG)" --user-id "$(USER)" --api-key "$(GEMINI_KEY)"

reindex:
	@test -n "$(CASE)" && test -n "$(ORG)" && test -n "$(USER)" || { echo "Faltan CASE= ORG= USER="; exit 2; }
	docker compose --env-file "$(ENV)" exec -T api python /srv/scripts/index_chunks_cli.py --case-id "$(CASE)" --org-id "$(ORG)" --user-id "$(USER)"

infra:
	docker network create judicial-net 2>/dev/null || true
	docker compose -p judicial-infra --env-file "$(ENV)" -f infra/docker/docker-compose.shared-infra.yml up -d

proxy:
	docker compose -p judicial-proxy -f docker-compose.shared-proxy.yml up -d

build:
	docker compose --env-file "$(ENV)" -f docker-compose.yml -f docker-compose.prod.yml build

up:
	ENV_FILE="$(ENV)" bash scripts/deploy.sh --shared --no-proxy

down:
	docker compose --env-file "$(ENV)" down

ps:
	@for p in judicial-infra judicial-proxy judicial-site judicial-develop judicial-quality; do \
		echo "== $$p =="; docker ps --filter "label=com.docker.compose.project=$$p" --format "table {{.Names}}\t{{.Status}}"; \
	done

logs:
	docker compose --env-file "$(ENV)" logs -f api
