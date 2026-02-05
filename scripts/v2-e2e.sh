#!/usr/bin/env bash
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"

PY="${PYTHON:-python3}"
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
fi

DEFAULT_TOKEN="dev-token"
if [[ -n "${GATEWAY_AUTH_TOKENS:-}" ]]; then
  DEFAULT_TOKEN="${GATEWAY_AUTH_TOKENS%%,*}"
fi
TOKEN="${TOKEN:-$DEFAULT_TOKEN}"
BRIDGE_HOST="${BRIDGE_HOST:-${HUE_BRIDGE_HOST:-}}"
ZONE_NAME="${ZONE_NAME:-}"

discover_bridge_host() {
  local out
  if command -v hue-gateway-discover >/dev/null 2>&1; then
    out="$(hue-gateway-discover --json --enrich 2>/dev/null || true)"
  else
    out="$(PYTHONPATH=src "$PY" -m hue_gateway.discover_tool --json --enrich 2>/dev/null || true)"
  fi

  if [[ -z "$out" ]]; then
    return 1
  fi

  echo "$out" | "$PY" -c 'import json,sys; data=json.load(sys.stdin); print((data[0].get("ip") if data else ""))'
}

if [[ -z "${BRIDGE_HOST}" ]]; then
  BRIDGE_HOST="$(discover_bridge_host || true)"
fi

if [[ -z "${BRIDGE_HOST}" ]]; then
  echo "Missing BRIDGE_HOST. Set BRIDGE_HOST=<ip> (or HUE_BRIDGE_HOST) and retry." >&2
  exit 2
fi

json() {
  "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin), separators=(",",":"), ensure_ascii=False))'
}

post() {
  local body="$1"
  curl -sS -X POST "${GATEWAY_URL}/v2/actions" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Request-Id: e2e-$(date +%s)" \
    -H "Content-Type: application/json" \
    -d "${body}" | json
  echo
}

echo "1) bridge.set_host (${BRIDGE_HOST})"
post "{\"action\":\"bridge.set_host\",\"args\":{\"bridgeHost\":\"${BRIDGE_HOST}\"}}"

# Give the server a moment to pick up the stored host (bootstrap loop).
ready_reason=""
for i in {1..5}; do
  ready_reason="$(curl -sS "${GATEWAY_URL}/readyz" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(r.get("reason") or "")')"
  [[ "${ready_reason}" != "missing_bridge_host" ]] && break
  sleep 1
done

if [[ "${ready_reason}" == "missing_application_key" ]]; then
  echo "2) bridge.pair (PRESS THE BRIDGE BUTTON FIRST)"
  echo "   Retrying for ~60s until the bridge accepts the press..."
  for i in {1..30}; do
    out="$(curl -sS -X POST "${GATEWAY_URL}/v2/actions" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "X-Request-Id: e2e-pair-$(date +%s)" \
      -H "Content-Type: application/json" \
      -d '{"action":"bridge.pair","args":{"devicetype":"hue-gateway#local"}}')"
    echo "${out}" | json
    ok="$(echo "${out}" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print("1" if r.get("ok") else "0")')"
    if [[ "${ok}" == "1" ]]; then
      break
    fi
    code="$(echo "${out}" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print((r.get("error") or {}).get("code") or "")')"
    if [[ "${code}" != "link_button_not_pressed" ]]; then
      echo "Pairing failed with error.code=${code}"
      exit 1
    fi
    sleep 2
  done
else
  echo "2) bridge.pair (skipped; readyz.reason=${ready_reason:-none})"
fi

echo "3) inventory.snapshot"
for i in {1..15}; do
  out="$(curl -sS -X POST "${GATEWAY_URL}/v2/actions" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Request-Id: e2e-$(date +%s)" \
    -H "Content-Type: application/json" \
    -d '{"action":"inventory.snapshot","args":{}}')"
  echo "${out}" | json
  rooms="$(echo "${out}" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(len((r.get("result") or {}).get("rooms") or []))')"
  zones="$(echo "${out}" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(len((r.get("result") or {}).get("zones") or []))')"
  if [[ -z "${ZONE_NAME}" ]]; then
    ZONE_NAME="$(echo "${out}" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); z=(r.get("result") or {}).get("zones") or []; print((z[0].get("name") if z else ""))')"
    if [[ -n "${ZONE_NAME}" ]]; then
      echo "Selected zone for dry-run: ${ZONE_NAME}"
    fi
  fi
  if [[ "${rooms}" != "0" || "${zones}" != "0" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "${ZONE_NAME}" ]]; then
  echo "No zones found in inventory; set ZONE_NAME explicitly and retry." >&2
  exit 1
fi

echo "4) resolve zone rid (rtype=zone name=${ZONE_NAME})"
post "{\"action\":\"resolve.by_name\",\"args\":{\"rtype\":\"zone\",\"name\":\"${ZONE_NAME}\"}}"

echo "5) zone.set dryRun impact only (zoneName=${ZONE_NAME})"
post "{\"action\":\"zone.set\",\"args\":{\"zoneName\":\"${ZONE_NAME}\",\"state\":{\"on\":false},\"dryRun\":true}}"

echo "Done."
