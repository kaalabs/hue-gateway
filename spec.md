# Spec: Hue Gateway (Dockerized API server for agentic “skill” control)

## 0) Decisions (locked in)
From your answers (2026-01-26):

- Deployment: **LAN-only**.
- Bridges: **single bridge**.
- Discovery: **manual bridge host only** (no `discovery.meethue.com` in product flow).
- Pairing: gateway **stores application key**.
- Gateway auth: accept **Bearer token OR X-API-Key**.
- Extra network hardening: **none**.
- TLS to Bridge: **always insecure** (skip certificate verification).
- Scope: **full resource pass-through** (Hue API v2 / CLIP v2), but…
- Tooling surface: **single control endpoint** (`POST /v1/actions`) rather than many typed endpoints.
- Name resolution: **fuzzy match**, `409` on ambiguity unless confidence is high.
- Ambiguity auto-pick: **auto-pick** if above threshold, else return `409` with candidates.
- Events: **include SSE** (`/v1/events/stream`).
- Cache: **maintain cache + keep updated via Bridge SSE**.
- Rate limiting/retries: **gateway rate limiting + backoff/retry** on Bridge `429/5xx`.
- Persistence: **SQLite** on a mounted volume.
- Logging: **minimal**.

## 1) Problem statement
Provide a dockerized HTTP API on the LAN that an agentic chat server can call as a “skill” to control Hue devices via a Hue Bridge (local Hue API v2 / CLIP v2), including:

- One-time pairing (requires physical bridge button press) to obtain and store a Hue application key.
- Ongoing control via a single LLM-friendly endpoint (`/v1/actions`) that can do:
  - high-level name-based operations
  - low-level “pass-through” to CLIP v2 resource endpoints
- Real-time updates via a simplified event stream endpoint.

## 2) Target architecture (runtime)
**Recommended stack (v1):**
- Python 3.12
- FastAPI + Uvicorn
- httpx for outbound HTTPS to Bridge (TLS verification disabled by design)
- SQLite for persistence (`/data/hue-gateway.db`)

**Runtime components:**
- HTTP API server
- Background worker that:
  - maintains an in-memory cache of resources/state
  - connects to Bridge `eventstream/clip/v2` (SSE) and applies updates
  - periodically re-syncs via `GET /clip/v2/resource/*` to heal gaps

## 3) Configuration
**Required (either env or stored in SQLite during setup):**
- `HUE_BRIDGE_HOST` (IP or hostname)
- `HUE_APPLICATION_KEY` (created via pairing; stored in SQLite)
- `GATEWAY_AUTH_TOKENS` (comma-separated) and/or `GATEWAY_API_KEYS` (comma-separated)

**Optional:**
- `PORT` (default 8000)
- `CACHE_RESYNC_SECONDS` (default 300)
- `FUZZY_MATCH_THRESHOLD` (default 0.90)
- `FUZZY_MATCH_AUTOPICK_THRESHOLD` (default 0.95)
- `RATE_LIMIT_RPS` (default 5 per credential)
- `RATE_LIMIT_BURST` (default 10 per credential)
- `RETRY_MAX_ATTEMPTS` (default 3)
- `RETRY_BASE_DELAY_MS` (default 200, exponential backoff + jitter)

**Config precedence:**
1) Explicit env vars (for containerized ops)
2) SQLite settings (for pairing + persistence)

## 4) Persistence (SQLite)
File: `/data/hue-gateway.db`

### Tables (minimum)
`settings`
- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`
- `updated_at INTEGER NOT NULL` (unix epoch seconds)

Suggested keys:
- `bridge_host`
- `application_key`

`resources`
- `rid TEXT PRIMARY KEY` (Hue resource id)
- `rtype TEXT NOT NULL` (e.g. `light`, `room`, `zone`, `scene`, `grouped_light`, `device`, …)
- `name TEXT` (best-effort human name)
- `json TEXT NOT NULL` (raw resource JSON)
- `updated_at INTEGER NOT NULL`

`name_index`
- `rtype TEXT NOT NULL`
- `name_norm TEXT NOT NULL`
- `rid TEXT NOT NULL`
- PRIMARY KEY (`rtype`, `name_norm`, `rid`)

Notes:
- Keep raw JSON for pass-through debugging and forward-compatibility.
- `name_index` supports fuzzy lookup without scanning huge JSON blobs.

## 5) Hue Bridge integration (outbound)

### Base URLs
- CLIP v2 resources: `https://{bridge_host}/clip/v2/resource/...`
- Event stream: `https://{bridge_host}/eventstream/clip/v2`

### Required headers
- `hue-application-key: <application_key>`

### TLS
- Always skip certificate verification (explicitly configured at HTTP client layer).

### Error handling
Normalize Bridge failures into:
- `424` for unreachable / DNS / connect timeout
- `502` for Bridge returned non-2xx with sanitized body
- `429` for rate-limited (gateway enforces its own rate limiting; also backs off on upstream 429)

### Retry policy
Only retry if **safe**:
- Retry: `GET`, `HEAD`, `OPTIONS`
- Retry `PUT`/`POST` only for specific action types declared idempotent (see §6), otherwise no retry.
Backoff:
- exponential backoff with jitter, max `RETRY_MAX_ATTEMPTS`
- stop early on success

## 6) HTTP API surface

### Authentication (required)
Requests must supply *either*:
- `Authorization: Bearer <token>` where `<token>` is in `GATEWAY_AUTH_TOKENS`
- `X-API-Key: <key>` where `<key>` is in `GATEWAY_API_KEYS`

On failure: `401` with `{ "error": "unauthorized" }`

### Health
- `GET /healthz` → `{ "ok": true }`
- `GET /readyz`
  - ready if:
    - `bridge_host` is set (env or DB)
    - `application_key` is set (env or DB)
    - a lightweight Bridge call succeeds (e.g. `GET /clip/v2/resource/bridge`)
  - response:
    - `{ "ready": true }` or `{ "ready": false, "reason": "..." }`

### Control: single entry point
- `POST /v1/actions`

#### Request envelope
```json
{
  "requestId": "optional-client-id",
  "action": "string",
  "args": { }
}
```

#### Response envelope
```json
{
  "requestId": "echoed",
  "action": "string",
  "ok": true,
  "result": { }
}
```
Errors use:
```json
{
  "requestId": "echoed",
  "action": "string",
  "ok": false,
  "error": {
    "code": "string",
    "message": "string",
    "details": { }
  }
}
```

#### Action catalog (v1)

1) `bridge.pair`
- Purpose: create/store Hue application key (requires physical button press).
- Args:
  - `devicetype` (optional; default `hue-gateway#docker`)
- Behavior:
  - Attempts user creation against the bridge
  - Stores `application_key` to SQLite
- Result:
  - `{ "applicationKey": "...", "stored": true }`
- Errors:
  - `424` if bridge unreachable
  - `409` if link button not pressed (surface as `code=link_button_not_pressed`)

2) `resolve.by_name`
- Purpose: name → rid (for agent visibility/debug).
- Args:
  - `rtype` (required)
  - `name` (required)
  - `mode` (optional: `fuzzy` default)
- Result:
  - `{ "matched": { "rid": "...", "rtype": "...", "name": "..." }, "confidence": 0.97 }`
- Errors:
  - `409` ambiguity with `{ candidates: [...] }`
  - `404` no match

3) `light.set` (high-level)
- Args (one of `rid` or `name` required):
  - `rid` OR `name`
  - `on` (optional boolean)
  - `brightness` (optional 0–100)
  - `colorTempK` (optional integer kelvin)
  - `xy` (optional `{ "x": number, "y": number }`)
- Behavior:
  - If `name` provided: resolve using fuzzy match policy (§7)
  - Translates into CLIP v2 `PUT /clip/v2/resource/light/{rid}` payload
- Idempotency: treat as idempotent if args fully specify desired state for the properties included.

4) `grouped_light.set` (high-level for rooms/zones)
- Same shape as `light.set` but targets `PUT /clip/v2/resource/grouped_light/{rid}`

5) `scene.activate` (high-level)
- Args: `rid` OR `name`
- Behavior: calls the appropriate CLIP v2 scene activation operation (implementation detail; map to Hue v2 semantics)
- Idempotency: treated as idempotent (activating same scene is safe)

6) `clipv2.request` (pass-through)
- Purpose: full resource pass-through for advanced/unknown endpoints.
- Args:
  - `method`: `GET|POST|PUT|DELETE`
  - `path`: must begin with `/clip/v2/` (server prepends `https://{bridge_host}`)
  - `body`: optional JSON object
- Behavior:
  - Validates `path` prefix and disallows host override
  - Adds `hue-application-key` header
  - Executes request and returns sanitized response
- Idempotency:
  - `GET` always idempotent
  - `PUT` idempotent
  - `POST/DELETE` not retried by default

## 7) Name resolution + fuzzy policy
Used when an action accepts `name`:

- Normalize names (lowercase, trim, collapse whitespace).
- Candidate set from `name_index` filtered by `rtype`.
- Fuzzy scoring (algorithm implementation detail; must output 0..1 confidence).
- If best score >= `FUZZY_MATCH_AUTOPICK_THRESHOLD`: **auto-pick**.
- Else if best score >= `FUZZY_MATCH_THRESHOLD` and the margin over #2 is large enough: **auto-pick**.
- Else: return `409` with candidates and scores; take **no action**.

Ambiguity response example:
```json
{
  "ok": false,
  "error": {
    "code": "ambiguous_name",
    "message": "Multiple matches for light name",
    "details": {
      "candidates": [
        { "rid": "...", "name": "Kitchen", "confidence": 0.92 },
        { "rid": "...", "name": "Kitchen (Island)", "confidence": 0.90 }
      ]
    }
  }
}
```

## 8) Events endpoint (gateway → clients)
- `GET /v1/events/stream`
  - Auth required
  - Response: `text/event-stream`
  - Each event data line is a single JSON object (no raw Hue SSE frames)

### Event model (normalized)
Emit events like:
```json
{
  "ts": "2026-01-26T23:59:59Z",
  "source": "hue-bridge",
  "type": "resource.updated",
  "resource": { "rid": "...", "rtype": "light" },
  "data": { }
}
```

### Bridge SSE lifecycle
- Background task maintains a single upstream connection to `eventstream/clip/v2`
- Reconnect on disconnect with backoff
- On reconnect, trigger a resync (full or partial) to ensure cache consistency

## 9) Rate limiting (gateway)
Per credential (token or api-key):
- Token bucket with `RATE_LIMIT_RPS` refill and `RATE_LIMIT_BURST` capacity
- On exceed: `429` with `{ "error": "rate_limited" }`

## 10) Docker + operational spec

### Container contract
- Listens on `0.0.0.0:${PORT}`
- Writes DB at `/data/hue-gateway.db`
- Runs as non-root

### Files
- Dockerfile
- docker-compose.yml (optional convenience)

### Healthcheck
Docker `HEALTHCHECK` hits `/healthz`

## 11) Out of scope (v1)
- Hue cloud/remote OAuth
- Entertainment streaming
- Additional network hardening (IP allowlist, mTLS)
- Rich logging/auditing

## 12) Acceptance criteria (v1)
- From a fresh install with `HUE_BRIDGE_HOST` set:
  - `POST /v1/actions` with `action=bridge.pair` succeeds after pressing the bridge button and stores the key in SQLite.
- With stored key:
  - `POST /v1/actions` `light.set` can toggle a known light by fuzzy name.
  - `POST /v1/actions` `clipv2.request` can `GET /clip/v2/resource/light` and returns Bridge JSON.
- `GET /v1/events/stream` streams normalized change events when a light is toggled from another client.
- Service is dockerized; restart preserves config/state via `/data` volume.

