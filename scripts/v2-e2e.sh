#!/usr/bin/env bash
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
TOKEN="${TOKEN:-dev-token}"
BRIDGE_HOST="${BRIDGE_HOST:-192.168.1.29}"
ZONE_NAME="${ZONE_NAME:-Keuken}"

json() {
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), separators=(",",":"), ensure_ascii=False))'
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

ready_reason="$(curl -sS "${GATEWAY_URL}/readyz" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("reason") or "")')"
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
    ok="$(echo "${out}" | python3 -c 'import json,sys; r=json.load(sys.stdin); print("1" if r.get("ok") else "0")')"
    if [[ "${ok}" == "1" ]]; then
      break
    fi
    code="$(echo "${out}" | python3 -c 'import json,sys; r=json.load(sys.stdin); print((r.get("error") or {}).get("code") or "")')"
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
post "{\"action\":\"inventory.snapshot\",\"args\":{}}"

echo "4) resolve Keuken zone rid (rtype=zone name=${ZONE_NAME})"
post "{\"action\":\"resolve.by_name\",\"args\":{\"rtype\":\"zone\",\"name\":\"${ZONE_NAME}\"}}"

echo "5) zone.set dryRun impact only (zoneName=${ZONE_NAME})"
post "{\"action\":\"zone.set\",\"args\":{\"zoneName\":\"${ZONE_NAME}\",\"state\":{\"on\":false},\"dryRun\":true}}"

echo "Done."
