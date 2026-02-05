# Hue Gateway

LAN-only, dockerized API server that controls Philips Hue via the Hue Bridge (Hue API v2 / CLIP v2). The API is designed for agentic “skill” use and exposes a single action endpoint.

## Status
Implementation in progress. Sources of truth:
- `research.md`
- `spec.md`
- `plan.md`
- `task.md`
- v2 spec baseline: `docs/change_requests/hue-gateway-api-architecture-0v91.md`
- v2 OpenAPI contract: `openapi-v2.skeleton.yaml` (published: `docs/spec/v2/openapi.yaml`)
- v2 semantic companion: `spec-v2.md` (published: `docs/spec/v2/semantic.md`)

## Local dev (Phase 0+)
Requirements: Python 3.12+

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn hue_gateway.app:app --reload --port 8000
```

Health:
```sh
curl -s http://localhost:8000/healthz
```

## Docker
```sh
docker compose up -d --build
```

## Ops helpers
- Make targets: `make help`
- Script (no executable bit required): `bash scripts/hue-gateway-ops.sh help`
- Interactive API docs: `make docs-up` then open `http://localhost:8081`

## Configuration (env vars)
Required for `/v1/*` and `/v2/*`:
- `GATEWAY_AUTH_TOKENS` (comma-separated Bearer tokens)
- `GATEWAY_API_KEYS` (comma-separated API keys)

Required to talk to the Hue Bridge:
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

## Security notes
- This project is **LAN-only** by design.
- Outbound TLS verification to the Hue Bridge is **disabled** (self-signed bridge certs); do not expose the gateway publicly.

## API
- `POST /v1/actions` (auth required; shape-frozen)
- `GET /v1/events/stream` (auth required; SSE)
- `POST /v2/actions` (auth required; canonical envelopes, idempotency, verify)
- `GET /v2/events/stream` (auth required; SSE with cursor resume via `Last-Event-ID`)

### v2 E2E (real bridge)
Run the gateway locally, then run:
```sh
make v2-e2e
```

## Pairing tool (operator)
Use the stand-alone pairing tool to set the bridge host and perform the button-press pairing loop:

```sh
hue-gateway-pair --gateway-url http://localhost:8000 \
  --token dev-token \
  --bridge-host 192.168.1.2 \
  --verify
```

## Discovery tool (operator)
`hue-gateway-discover` tries SSDP/UPnP first (fast), then optionally mDNS/zeroconf (requires `zeroconf` extra), and can optionally fall back to a CIDR scan of `http://<ip>/description.xml` (slow).

Examples:
```sh
hue-gateway-discover --enrich
hue-gateway-discover --scan-cidr 192.168.1.0/24 --enrich
```

### Auth headers
- `Authorization: Bearer <token>` OR
- `X-API-Key: <key>`

### Pairing (press the bridge button first)
```sh
curl -s -X POST http://localhost:8000/v1/actions \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"action":"bridge.pair","args":{"devicetype":"hue-gateway#docker"}}'
```

### Pass-through CLIP v2 request
```sh
curl -s -X POST http://localhost:8000/v1/actions \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"action":"clipv2.request","args":{"method":"GET","path":"/clip/v2/resource/light"}}'
```

### High-level light control (name-based)
```sh
curl -s -X POST http://localhost:8000/v1/actions \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"action":"light.set","args":{"name":"Kitchen","on":true,"brightness":30,"colorTempK":2700}}'
```
