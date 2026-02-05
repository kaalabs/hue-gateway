# Hue Gateway

LAN-only, dockerized API server that controls Philips Hue via a Hue Bridge (Hue CLIP v2). The API is designed for agentic tool-calling and exposes a single action endpoint.

Current release: **v2.0.0**.

## API (v2)
- `POST /v2/actions` (canonical envelopes, idempotency, verify)
- `GET /v2/events/stream` (SSE with cursor resume via `Last-Event-ID`)

Legacy compatibility:
- `/v1/*` remains behavior/shape-frozen for generated-client compatibility (`/v1/actions`, `/v1/events/stream`).

## Spec + docs
- Static v2 OpenAPI (contract): `docs/spec/v2/openapi.yaml`
- v2 semantic notes: `docs/spec/v2/semantic.md`
- Architecture + locked decisions baseline: `docs/change_requests/hue-gateway-api-architecture-0v91.md`
- Generated OpenAPI (implementation; includes `/v1/*` + `/v2/*`): `openapi.json` and `docs/openapi.json`

Interactive docs (Stoplight Elements):
```sh
make docs-up
open "http://localhost:8081/?specUrl=./spec/v2/openapi.yaml"
```

## Quick start (Docker)
```sh
docker compose up -d --build
```

Health / readiness:
```sh
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/readyz
```

## Configuration (env vars)
Required for `/v1/*` and `/v2/*`:
- `GATEWAY_AUTH_TOKENS` (comma-separated Bearer tokens)
- `GATEWAY_API_KEYS` (comma-separated API keys)

Bridge config (either env or one-time pairing):
- `HUE_BRIDGE_HOST` (IP/hostname on the LAN)
- `HUE_APPLICATION_KEY` (stored in `/data/hue-gateway.db` after pairing if not supplied via env)

Operational:
- `CACHE_RESYNC_SECONDS` (default `300`)
- `FUZZY_MATCH_THRESHOLD` (default `0.90`)
- `FUZZY_MATCH_AUTOPICK_THRESHOLD` (default `0.95`)
- `FUZZY_MATCH_MARGIN` (default `0.05`)
- `RATE_LIMIT_RPS` (default `5`)
- `RATE_LIMIT_BURST` (default `10`)
- `RETRY_MAX_ATTEMPTS` (default `3`)
- `RETRY_BASE_DELAY_MS` (default `200`)

## Pairing (v2)
Auth headers:
- `Authorization: Bearer <token>` OR
- `X-API-Key: <key>`

1) Set bridge host:
```sh
curl -s -X POST http://localhost:8000/v2/actions \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"action":"bridge.set_host","args":{"bridgeHost":"192.168.1.2"}}'
```

2) Press the physical Hue Bridge button, then pair:
```sh
curl -s -X POST http://localhost:8000/v2/actions \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"action":"bridge.pair","args":{"devicetype":"hue-gateway#docker"}}'
```

## v2 E2E (real bridge)
Run against a local gateway:
```sh
make v2-e2e
```

Useful overrides:
- `GATEWAY_URL` (default `http://localhost:8000`)
- `TOKEN` (default: first token in `GATEWAY_AUTH_TOKENS` or `dev-token`)
- `BRIDGE_HOST` (defaults to `HUE_BRIDGE_HOST` if set, else auto-discovered)
- `ZONE_NAME` (defaults to the first zone name in `inventory.snapshot`)

## Local dev
Requirements: Python 3.12+

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn hue_gateway.app:app --reload --port 8000
```

Tests:
```sh
.venv/bin/python -m pytest -q
```

## Operator tools
- Pairing tool: `hue-gateway-pair --help`
- Bridge discovery: `hue-gateway-discover --help`

## Ops helpers
- Make targets: `make help`
- Script (no executable bit required): `bash scripts/hue-gateway-ops.sh help`
- Interactive API docs: `make docs-up` then open `http://localhost:8081`

## Security notes
- This project is **LAN-only** by design.
- Outbound TLS verification to the Hue Bridge is **disabled** (self-signed bridge certs); do not expose the gateway publicly.
