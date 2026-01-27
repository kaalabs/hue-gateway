# Implementation Plan (phased) — Hue Gateway

This plan is derived from `spec.md` and is organized as incremental, shippable phases.

---

## Phase 0 — Repo scaffold
- Add Python project skeleton (`src/`, `pyproject.toml` or `requirements.txt`, minimal README).
- Add FastAPI app entrypoint with `/healthz` and `/readyz` stubs.
- Add baseline configuration loader (env first, then SQLite).
- Add local dev commands (Makefile or scripts) to run the server.

**Exit criteria**
- `curl /healthz` returns `{ "ok": true }`.
- App starts locally with no Hue config (readiness can be false).

---

## Phase 1 — Docker & runtime contract
- Add `Dockerfile` (non-root user, exposes `${PORT}`, `HEALTHCHECK /healthz`).
- Add `docker-compose.yml` (mount `/data`, map port, env vars for auth tokens/keys).
- Ensure `/data` is created and writable at runtime.

**Exit criteria**
- `docker compose up` starts service, `/healthz` works, `/readyz` returns not-ready without config.
- Container restart preserves `/data` volume.

---

## Phase 2 — SQLite persistence layer
- Create SQLite schema and migrations/bootstrap on startup:
  - `settings`, `resources`, `name_index` (per `spec.md`)
- Implement a small persistence module:
  - `get_setting/set_setting`
  - `upsert_resource`, `rebuild_name_index` (or incremental updates)

**Exit criteria**
- Service creates `/data/hue-gateway.db` and reads/writes `settings`.

---

## Phase 3 — Auth middleware + minimal logging
- Implement auth:
  - Accept `Authorization: Bearer` OR `X-API-Key`
  - Compare against configured lists
- Ensure minimal logging (no secrets, no request bodies).

**Exit criteria**
- All `/v1/*` endpoints reject unauthenticated requests with `401`.
- Health endpoints optionally unauthenticated (choose and document).

---

## Phase 4 — Hue Bridge client (outbound)
- Implement Hue HTTP client using `httpx`:
  - Base URL construction for `https://{bridge_host}`
  - Always skip TLS verification (as per spec decision)
  - Add `hue-application-key` header when available
- Normalize errors:
  - unreachable → `424`
  - non-2xx → `502` with sanitized details
  - upstream `429` propagated

**Exit criteria**
- A single function can call CLIP v2 endpoints and return structured results/errors.

---

## Phase 5 — `/healthz` and `/readyz` fully implemented
- `readyz` checks:
  - `bridge_host` present
  - `application_key` present
  - Bridge connectivity with a lightweight request (e.g. `GET /clip/v2/resource/bridge`)

**Exit criteria**
- With no config: `ready=false`.
- With valid config: `ready=true`.

---

## Phase 6 — Actions API skeleton (`POST /v1/actions`)
- Implement request/response envelopes exactly as in `spec.md`.
- Add action dispatch registry and shared error helpers.
- Add input validation (Pydantic models).

**Exit criteria**
- `POST /v1/actions` returns consistent `ok/error` envelopes for unknown actions and bad inputs.

---

## Phase 7 — Pairing action: `bridge.pair`
- Implement Hue user/app-key creation (button-press flow).
- Store `application_key` in SQLite `settings`.
- Return `{ applicationKey, stored: true }`.

**Exit criteria**
- After pressing bridge button, `bridge.pair` succeeds and persists key.
- Without button press, returns a clear `409` (`link_button_not_pressed`).

---

## Phase 8 — Pass-through action: `clipv2.request`
- Implement path validation:
  - must start with `/clip/v2/`
  - must not allow host override
- Implement method allowlist and body forwarding.
- Apply retry rules:
  - retry safe methods (`GET/HEAD/OPTIONS`) on transient errors and upstream `429/5xx`
  - do not retry `POST/DELETE` by default

**Exit criteria**
- Can `GET /clip/v2/resource/light` via gateway and receive Bridge JSON.

---

## Phase 9 — Cache bootstrap + resync loop
- On startup (if configured), fetch core resources into `resources`:
  - at minimum: `device`, `light`, `room`, `zone`, `grouped_light`, `scene`
- Maintain in-memory cache for quick name lookups and action execution.
- Periodic resync (`CACHE_RESYNC_SECONDS`) to heal missed updates.

**Exit criteria**
- Cache and DB contain up-to-date inventories after initial sync.

---

## Phase 10 — SSE ingestion + `/v1/events/stream`
- Implement upstream SSE connection to `https://{bridge_host}/eventstream/clip/v2`.
- Parse events and apply updates to:
  - in-memory cache
  - SQLite `resources` (and name index when names change)
- Expose `/v1/events/stream`:
  - Auth required
  - Emits normalized event objects

**Exit criteria**
- Toggling a light externally produces a normalized event to connected clients.

---

## Phase 11 — Name resolution + fuzzy policy
- Build normalization (`name_norm`) and candidate retrieval from `name_index`.
- Implement fuzzy scoring and thresholds:
  - auto-pick above `FUZZY_MATCH_AUTOPICK_THRESHOLD`
  - else pick if above threshold and margin sufficient
  - else `409` with candidates
- Implement `resolve.by_name` action.

**Exit criteria**
- `resolve.by_name` reliably returns correct rid or `409` with candidates.

---

## Phase 12 — High-level actions
- Implement:
  - `light.set` → `PUT /clip/v2/resource/light/{rid}`
  - `grouped_light.set` → `PUT /clip/v2/resource/grouped_light/{rid}`
  - `scene.activate` → Hue v2 scene activation semantics
- Implement input normalization:
  - `brightness` 0–100
  - `colorTempK` → `mirek`
  - `xy` pass-through

**Exit criteria**
- Can control lights/rooms/zones/scenes by fuzzy name via `/v1/actions`.

---

## Phase 13 — Rate limiting + backoff/retry hardening
- Add per-credential token bucket limiter on the gateway.
- Ensure responses:
  - gateway limit → `429` (`rate_limited`)
  - upstream `429` triggers backoff for safe retries
- Prevent retry loops and thundering-herd reconnects (jittered delays).

**Exit criteria**
- Sustained load is limited per credential; gateway remains responsive.

---

## Phase 14 — Validation, tests, and docs
- Add focused tests:
  - auth middleware
  - action envelope validation
  - path validation for `clipv2.request`
  - name resolution ambiguity behavior (unit tests)
- Add docs:
  - Setup steps (bridge host, pairing, auth config)
  - Example `curl` invocations for key actions
  - Operational notes (LAN-only, TLS insecure decision)

**Exit criteria**
- Tests pass locally/CI (if added).
- README is sufficient to run end-to-end on a LAN with a Hue Bridge.

---

## Phase 15 — Release checklist
- Verify minimal logging (no secrets).
- Verify persistence works across restarts.
- Verify SSE reconnect/resync behavior.
- Freeze the OpenAPI schema and include it as an artifact for the agentic chat server tooling.

