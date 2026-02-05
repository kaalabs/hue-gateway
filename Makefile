.PHONY: help up down restart build rebuild pull logs logs-follow ps shell health ready openapi openapi-fetch \
        publish-spec docker-clean db-path db-shell db-backup events v2-events v2-e2e docs-up docs-down docs-logs docs-open

COMPOSE ?= docker compose
SERVICE ?= hue-gateway
PORT ?= 8000
PYTHON ?= python3

ifneq ("$(wildcard .venv/bin/python)","")
PYTHON := .venv/bin/python
endif

help:
	@echo "Hue Gateway ops:"
	@echo "  make up            - build + start in background"
	@echo "  make down          - stop + remove containers (keeps volume)"
	@echo "  make restart       - restart service"
	@echo "  make build         - build image"
	@echo "  make rebuild       - rebuild without cache"
	@echo "  make pull          - pull base images"
	@echo "  make ps            - show compose status"
	@echo "  make logs          - show recent logs"
	@echo "  make logs-follow   - follow logs"
	@echo "  make shell         - shell into container"
	@echo "  make health        - curl /healthz"
	@echo "  make ready         - curl /readyz"
	@echo "  make openapi       - generate openapi.json from code"
	@echo "  make openapi-fetch - fetch openapi.json from running gateway"
	@echo "  make publish-spec  - publish v2 spec artifacts into ./docs/"
	@echo "  make db-shell      - open sqlite shell for /data/hue-gateway.db"
	@echo "  make db-backup     - backup sqlite to ./backups/"
	@echo "  make events        - curl SSE stream (Ctrl+C to stop)"
	@echo "  make v2-events     - curl v2 SSE stream (Ctrl+C to stop)"
	@echo "  make v2-e2e        - run basic v2 E2E flow (local server)"
	@echo "  make docs-up       - start api-docs at http://localhost:8081"
	@echo "  make docs-down     - stop api-docs"
	@echo "  make docs-logs     - api-docs logs"
	@echo "  make docs-open     - open docs URL (macOS)"
	@echo "  make docker-clean  - remove hue-gateway docker containers/images (keeps volumes)"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart $(SERVICE)

build:
	$(COMPOSE) build

rebuild:
	$(COMPOSE) build --no-cache

pull:
	$(COMPOSE) pull

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=200 --no-color $(SERVICE)

logs-follow:
	$(COMPOSE) logs -f --no-color $(SERVICE)

shell:
	$(COMPOSE) exec $(SERVICE) sh

health:
	@curl -s http://localhost:$(PORT)/healthz && echo

ready:
	@curl -s http://localhost:$(PORT)/readyz && echo

openapi:
	@$(PYTHON) -c 'import json,sys; sys.path.insert(0,"src"); from hue_gateway.app import app; open("openapi.json","w").write(json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False))'
	@mkdir -p docs
	@cp openapi.json docs/openapi.json
	@$(PYTHON) -c "import json; json.load(open('openapi.json')); print('wrote openapi.json')"

openapi-fetch:
	@curl -fsS http://localhost:$(PORT)/openapi.json > openapi.json
	@mkdir -p docs
	@cp openapi.json docs/openapi.json
	@$(PYTHON) -c "import json; json.load(open('openapi.json')); print('wrote openapi.json')"

publish-spec:
	@$(MAKE) openapi
	@mkdir -p docs/spec/v2
	@cp openapi-v2.skeleton.yaml docs/spec/v2/openapi.yaml
	@cp spec-v2.md docs/spec/v2/semantic.md

docker-clean:
	@bash scripts/docker-clean-hue-gateway.sh

db-path:
	@$(COMPOSE) exec -T $(SERVICE) sh -lc 'echo /data/hue-gateway.db'

db-shell:
	@$(COMPOSE) exec -T $(SERVICE) sh -lc 'sqlite3 /data/hue-gateway.db'

db-backup:
	@mkdir -p backups
	@ts=$$(date +%Y%m%d-%H%M%S); \
	$(COMPOSE) exec -T $(SERVICE) sh -lc 'sqlite3 /data/hue-gateway.db ".backup /tmp/hue-gateway-$${ts}.db"' && \
	$(COMPOSE) cp $(SERVICE):/tmp/hue-gateway-$${ts}.db backups/hue-gateway-$${ts}.db && \
	$(COMPOSE) exec -T $(SERVICE) sh -lc 'rm -f /tmp/hue-gateway-'$${ts}'.db' && \
	echo "backed up to backups/hue-gateway-$${ts}.db"

events:
	@curl -N -s -H 'Authorization: Bearer dev-token' http://localhost:$(PORT)/v1/events/stream

v2-events:
	@curl -N -s -H 'Authorization: Bearer dev-token' http://localhost:$(PORT)/v2/events/stream

v2-e2e:
	@bash scripts/v2-e2e.sh

docs-up:
	@$(MAKE) openapi
	$(COMPOSE) up -d api-docs

docs-down:
	$(COMPOSE) stop api-docs

docs-logs:
	$(COMPOSE) logs --tail=200 --no-color api-docs

docs-open:
	@open http://localhost:8081
