# Tasks (sequential) — Hue Gateway

Rule: **Do not start Phase N+1 until the final validation task(s) in Phase N complete successfully.**

---

## Phase 0 — Repo scaffold
- [ ] Create Python project structure: `src/hue_gateway/`, `src/hue_gateway/__init__.py`.
- [ ] Add dependency management (`pyproject.toml` + lock, or `requirements.txt` + `requirements-dev.txt`).
- [ ] Add FastAPI app entrypoint (e.g. `src/hue_gateway/app.py`) with `/healthz` and `/readyz` (stub).
- [ ] Add config loader module:
  - [ ] Read env vars for `PORT`, `HUE_BRIDGE_HOST`, `HUE_APPLICATION_KEY`, `GATEWAY_AUTH_TOKENS`, `GATEWAY_API_KEYS`.
  - [ ] Stub “then SQLite” config (actual DB comes Phase 2).
- [ ] Add local run command (Makefile target or script) to start Uvicorn.
- [ ] Add minimal `README.md` with local run instructions and current limitations.
- [ ] Validate DoD: start server locally and `curl http://localhost:${PORT}/healthz` returns `{ "ok": true }`.
- [ ] Validate DoD: start server locally with no Hue config and confirm `/readyz` returns `ready=false` (or equivalent “not ready” response).

---

## Phase 1 — Docker & runtime contract
- [ ] Add `Dockerfile`:
  - [ ] Uses slim base image.
  - [ ] Creates non-root user.
  - [ ] Installs deps.
  - [ ] Exposes `${PORT}` and runs Uvicorn.
  - [ ] Adds `HEALTHCHECK` hitting `/healthz`.
- [ ] Add `.dockerignore` (exclude venv, caches, git, local db, etc.).
- [ ] Add `docker-compose.yml`:
  - [ ] Maps `${PORT}`.
  - [ ] Mounts a named volume (or bind mount) to `/data`.
  - [ ] Passes auth env vars.
- [ ] Ensure runtime creates `/data` if missing and can write into it (even before SQLite).
- [ ] Validate DoD: `docker compose up` and `curl /healthz` works; `curl /readyz` is not-ready without config.
- [ ] Validate DoD: restart container and confirm the `/data` volume persists (file created in `/data` remains).

---

## Phase 2 — SQLite persistence layer
- [ ] Add SQLite bootstrap on startup:
  - [ ] Create/open `/data/hue-gateway.db`.
  - [ ] Create tables: `settings`, `resources`, `name_index` (per `spec.md`).
- [ ] Implement persistence module:
  - [ ] `get_setting(key)` / `set_setting(key,value)`.
  - [ ] `upsert_resource(rid,rtype,name,json,updated_at)`.
  - [ ] `rebuild_name_index()` (or incremental updates).
- [ ] Wire config loader to read `bridge_host` + `application_key` from SQLite when not present in env.
- [ ] Validate DoD: run container and verify `/data/hue-gateway.db` is created automatically.
- [ ] Validate DoD: verify settings read/write works end-to-end (set a dummy setting, restart, and read it back).

---

## Phase 3 — Auth middleware + minimal logging
- [ ] Define which endpoints require auth:
  - [ ] `/v1/*` requires auth.
  - [ ] Decide/document whether `/healthz` and `/readyz` are public or require auth.
- [ ] Implement auth verifier:
  - [ ] Accept `Authorization: Bearer <token>` OR `X-API-Key: <key>`.
  - [ ] Parse multiple tokens/keys from env (comma-separated).
  - [ ] Constant-time-ish compare (avoid obvious timing leaks).
- [ ] Add minimal request logging:
  - [ ] No secrets (no auth headers, no Hue application key).
  - [ ] No request bodies.
  - [ ] Include request id if provided (or generated).
- [ ] Validate DoD: `POST /v1/...` without auth returns `401`.
- [ ] Validate DoD: with valid Bearer token and with valid X-API-Key, `/v1/...` succeeds (at least reaches handler).

---

## Phase 4 — Hue Bridge client (outbound)
- [ ] Implement Hue HTTP client module (httpx):
  - [ ] Base URL: `https://{bridge_host}`.
  - [ ] Always skip TLS verification.
  - [ ] Add `hue-application-key` header when available.
  - [ ] Timeouts (connect/read) configurable.
- [ ] Implement error normalization:
  - [ ] DNS/connect/timeout → `424` (failed dependency) in API layer.
  - [ ] Non-2xx → `502` with sanitized error details.
  - [ ] Preserve upstream `429`.
- [ ] Add a single “raw request” function that can call CLIP v2 paths and return `{status, headers?, json/text}`.
- [ ] Validate DoD: unit-test the client error mapping with mocked httpx responses (unreachable, 502, 429).
- [ ] Validate DoD: smoke-test with a real bridge (if available) that a `GET /clip/v2/resource/bridge` call returns structured JSON.

---

## Phase 5 — `/healthz` and `/readyz` fully implemented
- [ ] Implement readiness checks:
  - [ ] Determine `bridge_host` source (env or SQLite).
  - [ ] Determine `application_key` source (env or SQLite).
  - [ ] Perform lightweight bridge call (`GET /clip/v2/resource/bridge`) using Hue client.
- [ ] Ensure `/readyz` returns structured reason when not ready (missing config vs bridge unreachable vs unauthorized key).
- [ ] Validate DoD: with no config, `/readyz` returns `ready=false`.
- [ ] Validate DoD: with valid `bridge_host` + `application_key`, `/readyz` returns `ready=true`.

---

## Phase 6 — Actions API skeleton (`POST /v1/actions`)
- [ ] Implement `POST /v1/actions` endpoint with request/response envelopes from `spec.md`.
- [ ] Add Pydantic models for:
  - [ ] envelope (`requestId`, `action`, `args`)
  - [ ] standardized error response
- [ ] Add action dispatch registry (string → handler) and shared helpers.
- [ ] Add default handling for unknown actions and invalid args.
- [ ] Validate DoD: unknown `action` returns consistent `ok=false` envelope.
- [ ] Validate DoD: invalid payload (missing fields/wrong types) returns consistent `ok=false` envelope.

---

## Phase 7 — Pairing action: `bridge.pair`
- [ ] Implement `bridge.pair` action handler:
  - [ ] Use `bridge_host` from config/env.
  - [ ] Call Hue API user creation flow (button-press required).
  - [ ] Extract `applicationKey` from response.
  - [ ] Store `application_key` in SQLite `settings`.
- [ ] Ensure response matches `{ applicationKey, stored: true }`.
- [ ] Map “button not pressed” to `409` with `code=link_button_not_pressed`.
- [ ] Validate DoD: press bridge button, call `bridge.pair`, confirm success and key persisted to SQLite.
- [ ] Validate DoD: without pressing button, call `bridge.pair`, confirm `409 link_button_not_pressed`.

---

## Phase 8 — Pass-through action: `clipv2.request`
- [ ] Implement `clipv2.request` handler:
  - [ ] Validate `path` begins with `/clip/v2/`.
  - [ ] Disallow host override (`http://`, `https://`, `//`, `..` traversal).
  - [ ] Validate `method` allowlist.
  - [ ] Forward JSON body for relevant methods.
  - [ ] Return sanitized response in the action envelope.
- [ ] Implement retry policy:
  - [ ] Retry `GET/HEAD/OPTIONS` on transient failures and upstream `429/5xx` with backoff + jitter.
  - [ ] Do not retry `POST/DELETE` by default.
  - [ ] Cap attempts and total time.
- [ ] Validate DoD: calling `clipv2.request` with `GET /clip/v2/resource/light` returns Bridge JSON through the gateway.
- [ ] Validate DoD: path validation rejects invalid prefixes/host overrides with a clear `400`-class error.

---

## Phase 9 — Cache bootstrap + resync loop
- [ ] Define “core resources” list and implement initial sync:
  - [ ] `device`, `light`, `room`, `zone`, `grouped_light`, `scene`.
- [ ] Persist resources into SQLite `resources` and rebuild/maintain `name_index`.
- [ ] Build in-memory cache structure (by `rid`, and name lookup per `rtype`).
- [ ] Implement periodic resync job (`CACHE_RESYNC_SECONDS`):
  - [ ] Runs in background.
  - [ ] Heals missed updates.
  - [ ] Avoids overlapping runs.
- [ ] Validate DoD: after startup with valid config, DB contains inventories for the core resources.
- [ ] Validate DoD: resync loop runs and updates cache/DB when bridge inventory changes (manual verification acceptable).

---

## Phase 10 — SSE ingestion + `/v1/events/stream`
- [ ] Implement upstream SSE consumer:
  - [ ] Connect to `https://{bridge_host}/eventstream/clip/v2`.
  - [ ] Reconnect with backoff + jitter on disconnect.
  - [ ] On reconnect, trigger a resync (full or partial).
- [ ] Implement event parser and updater:
  - [ ] Apply updates to in-memory cache.
  - [ ] Apply updates to SQLite `resources` (and name index as needed).
- [ ] Implement `GET /v1/events/stream` endpoint:
  - [ ] Auth required.
  - [ ] Emits normalized event objects (`resource.updated`, etc.).
- [ ] Validate DoD: toggle a light externally; confirm a normalized event is emitted to a connected `/v1/events/stream` client.
- [ ] Validate DoD: disconnect/reconnect behavior works (force upstream drop; confirm reconnect + resync happens).

---

## Phase 11 — Name resolution + fuzzy policy
- [ ] Implement name normalization (`name_norm`) consistent across indexing and lookup.
- [ ] Implement candidate retrieval from `name_index` filtered by `rtype`.
- [ ] Implement fuzzy scoring (0..1) and selection policy:
  - [ ] Auto-pick above `FUZZY_MATCH_AUTOPICK_THRESHOLD`.
  - [ ] Else auto-pick if above threshold and margin sufficient.
  - [ ] Else return `409` with candidates and confidences; take no action.
- [ ] Implement `resolve.by_name` action.
- [ ] Validate DoD: `resolve.by_name` returns correct rid for clear matches (manual test on real bridge inventory).
- [ ] Validate DoD: ambiguous names produce `409` with candidates; clear best match above threshold auto-picks.

---

## Phase 12 — High-level actions
- [ ] Implement `light.set`:
  - [ ] Accept `rid` or `name`.
  - [ ] Map `brightness` to Hue v2 `dimming.brightness`.
  - [ ] Map `colorTempK` → `mirek` (clamp where appropriate).
  - [ ] Support `xy`.
- [ ] Implement `grouped_light.set` similarly.
- [ ] Implement `scene.activate`:
  - [ ] Resolve `rid` or `name`.
  - [ ] Call the correct CLIP v2 operation for activation.
- [ ] Ensure actions use name resolution policy from Phase 11.
- [ ] Validate DoD: control lights/rooms/zones/scenes by fuzzy name via `/v1/actions` on a real bridge.
- [ ] Validate DoD: invalid args and unsupported capabilities return clear `400/409/502`-class errors without partial side effects.

---

## Phase 13 — Rate limiting + backoff/retry hardening
- [ ] Implement per-credential token bucket limiter:
  - [ ] Key by Bearer token value or API key value.
  - [ ] Configurable RPS/burst.
- [ ] Apply limiter to `/v1/*` (including `/v1/events/stream` policy decision; document).
- [ ] Harden retry/backoff:
  - [ ] Ensure retries never loop indefinitely.
  - [ ] Ensure SSE reconnects are jittered.
  - [ ] Ensure upstream `429` triggers backoff for safe methods only.
- [ ] Validate DoD: sustained load exceeds limits and gateway responds with `429 rate_limited` while staying responsive.
- [ ] Validate DoD: upstream `429` causes backoff (observe via logs/metrics or controlled test) without cascading failures.

---

## Phase 14 — Validation, tests, and docs
- [ ] Add test harness:
  - [ ] Choose pytest (recommended) and configure.
  - [ ] Add httpx mocking strategy for Hue client.
- [ ] Add focused tests:
  - [ ] Auth middleware accepts/rejects correctly.
  - [ ] Action envelope validation (unknown action, invalid payload).
  - [ ] `clipv2.request` path validation.
  - [ ] Name resolution ambiguity logic (threshold/margin cases).
- [ ] Add docs:
  - [ ] `README.md` setup: bridge host, pairing, auth config, run with Docker.
  - [ ] Example `curl` for `bridge.pair`, `light.set`, `clipv2.request`, and consuming `/v1/events/stream`.
  - [ ] Operational notes: LAN-only, TLS insecure decision, persistence location.
- [ ] Validate DoD: run full test suite locally (and in container if feasible) and confirm all tests pass.
- [ ] Validate DoD: follow README from scratch on a LAN to achieve pairing + control + events (manual verification).

---

## Phase 15 — Release checklist
- [ ] Confirm logs remain minimal and never print secrets (auth values, Hue application key).
- [ ] Confirm persistence across restarts:
  - [ ] application key remains stored
  - [ ] resource cache repopulates and stabilizes
- [ ] Confirm SSE behavior:
  - [ ] reconnect backoff works
  - [ ] resync on reconnect works
- [ ] Freeze and export OpenAPI schema:
  - [ ] ensure `/openapi.json` is stable
  - [ ] save a copy under version control (e.g. `openapi.json`) for skill tooling if desired
- [ ] Validate DoD: complete an end-to-end smoke run (Dockerized) that demonstrates pairing, a `light.set`, a `clipv2.request`, and an event observed on `/v1/events/stream`.

