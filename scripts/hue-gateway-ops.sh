#!/usr/bin/env bash
set -euo pipefail

COMPOSE_BIN="${COMPOSE_BIN:-docker compose}"
SERVICE="${SERVICE:-hue-gateway}"
PORT="${PORT:-8000}"

cmd="${1:-help}"
shift || true

usage() {
  cat <<EOF
Usage: bash scripts/hue-gateway-ops.sh <command>

Core:
  up                 Build + start in background
  down               Stop + remove containers (keeps volume)
  restart            Restart service
  build              Build image
  rebuild            Rebuild without cache
  ps                 Show compose status

Logs:
  logs               Show recent logs
  logs-follow         Follow logs

Debug:
  shell              Shell into container
  health             GET /healthz
  ready              GET /readyz
  openapi            Export ./openapi.json from running gateway
  publish-spec       Publish v2 spec artifacts into ./docs/
  docs-up            Start interactive API docs at http://localhost:8081
  docs-down          Stop interactive API docs

Data:
  db-shell           Open sqlite shell on /data/hue-gateway.db
  db-backup          Backup sqlite to ./backups/

Helpers:
  set-bridge-host <ip>   Store bridge host via bridge.set_host
  pair                    Run pairing loop (press bridge button)
  discover                Discover Hue bridge(s) on LAN (operator tool)

Env:
  COMPOSE_BIN="docker compose"
  SERVICE="hue-gateway"
  PORT="8000"
  TOKEN="dev-token" (required for set-bridge-host/pair)
  GATEWAY_URL="http://localhost:8000"
EOF
}

gateway_url() {
  echo "${GATEWAY_URL:-http://localhost:${PORT}}"
}

token() {
  if [[ -n "${TOKEN:-}" ]]; then
    echo "$TOKEN"
    return
  fi
  echo "Missing TOKEN env var (e.g. TOKEN=dev-token)" >&2
  exit 2
}

case "$cmd" in
  help|-h|--help) usage ;;
  up) ${COMPOSE_BIN} up -d --build ;;
  down) ${COMPOSE_BIN} down ;;
  restart) ${COMPOSE_BIN} restart "${SERVICE}" ;;
  build) ${COMPOSE_BIN} build ;;
  rebuild) ${COMPOSE_BIN} build --no-cache ;;
  ps) ${COMPOSE_BIN} ps ;;

  logs) ${COMPOSE_BIN} logs --tail=200 --no-color "${SERVICE}" ;;
  logs-follow) ${COMPOSE_BIN} logs -f --no-color "${SERVICE}" ;;

  shell) ${COMPOSE_BIN} exec "${SERVICE}" sh ;;
  health) curl -s "$(gateway_url)/healthz" && echo ;;
  ready) curl -s "$(gateway_url)/readyz" && echo ;;
  openapi)
    curl -s "$(gateway_url)/openapi.json" > openapi.json
    mkdir -p docs
    cp openapi.json docs/openapi.json
    python3 -c "import json; json.load(open('openapi.json')); print('wrote openapi.json')"
    ;;
  publish-spec)
    bash -lc 'make publish-spec'
    ;;
  docs-up)
    curl -s "$(gateway_url)/openapi.json" > openapi.json
    mkdir -p docs
    cp openapi.json docs/openapi.json
    ${COMPOSE_BIN} up -d api-docs
    echo "Docs: http://localhost:8081"
    ;;
  docs-down)
    ${COMPOSE_BIN} stop api-docs
    ;;

  db-shell) ${COMPOSE_BIN} exec -T "${SERVICE}" sh -lc 'sqlite3 /data/hue-gateway.db' ;;
  db-backup)
    mkdir -p backups
    ts="$(date +%Y%m%d-%H%M%S)"
    ${COMPOSE_BIN} exec -T "${SERVICE}" sh -lc "sqlite3 /data/hue-gateway.db \".backup /tmp/hue-gateway-${ts}.db\""
    ${COMPOSE_BIN} cp "${SERVICE}:/tmp/hue-gateway-${ts}.db" "backups/hue-gateway-${ts}.db"
    ${COMPOSE_BIN} exec -T "${SERVICE}" sh -lc "rm -f /tmp/hue-gateway-${ts}.db"
    echo "backed up to backups/hue-gateway-${ts}.db"
    ;;

  set-bridge-host)
    ip="${1:-}"
    if [[ -z "$ip" ]]; then
      echo "Usage: set-bridge-host <bridge-ip>" >&2
      exit 2
    fi
    curl -sS -X POST "$(gateway_url)/v1/actions" \
      -H "Authorization: Bearer $(token)" \
      -H "Content-Type: application/json" \
      -d "{\"action\":\"bridge.set_host\",\"args\":{\"bridgeHost\":\"${ip}\"}}"
    echo
    ;;

  pair)
    python3 -m hue_gateway.pair_tool --gateway-url "$(gateway_url)" --token "$(token)" --verify
    ;;

  discover)
    python3 -m hue_gateway.discover_tool --enrich
    ;;

  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
