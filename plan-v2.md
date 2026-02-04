# Implementation Plan — Hue Gateway API v2.0

Status: Draft  
Last updated: 2026-02-04  

Inputs / sources of truth:
- `docs/change_requests/hue-gateway-api-architecture-0v91.md`
- `openapi-v2.skeleton.yaml`
- `spec-v2.md`

Constraints:
- `/v1/*` must remain behavior/shape-frozen for generated-client compatibility.
- v2 is delivered via `/v2/actions` + `/v2/events/stream`.
- Canonical v2 error envelope across *all* statuses (incl. `401`, `429`).

---

## Phase 0 — Prep, guardrails, and baselines

Goal: ensure we can iterate safely without regressing v1.

Tasks:
- [ ] Run existing test suite; fix only v2-adjacent failures.
- [ ] Add “v1 regression guard” tests (snapshot key response shapes for:
  - `POST /v1/actions` success + failure
  - `GET /v1/events/stream` basic streaming)
- [ ] Decide on file/module layout for v2:
  - recommended: add `src/hue_gateway/v2/` (schemas, router, dispatcher, verify, idempotency, sse)
  - keep existing v1 modules stable

Exit criteria:
- v1 tests green and basic v1 shape snapshots exist.

---

## Phase 1 — v2 routing + schema scaffolding

Goal: add `/v2/*` endpoints without implementing behavior yet.

Dependencies: Phase 0.

Tasks:
- [ ] Add `/v2/actions` route:
  - request: discriminated union (`action`) for v2 action catalog
  - response: 200 success union + non-2xx canonical error envelope models
  - include OpenAPI response models for `400/401/404/409/424/429/500/502` (+ `207` for batch)
- [ ] Add `/v2/events/stream` route stub:
  - returns `text/event-stream`
  - auth required
  - supports `Last-Event-ID`
- [ ] Ensure FastAPI OpenAPI emits `oneOf + discriminator` for `/v2/actions` (similar to v1 custom OpenAPI tightening).

Exit criteria:
- Server boots with both `/v1/*` and `/v2/*` routes.
- `/v2/actions` rejects unauthenticated with canonical v2 `401` envelope (even if implementation is stubbed).

---

## Phase 2 — Canonical v2 error model + response headers

Goal: make the v2 envelope a hard contract for every error status.

Dependencies: Phase 1.

Tasks:
- [ ] Implement v2 auth dependency that returns canonical `401 unauthorized`:
  - include `error.code=unauthorized`
  - include `X-Request-Id` response header when present
- [ ] Implement v2 request validation normalization:
  - invalid JSON → `400 invalid_json`
  - schema/args errors → `400 invalid_request`
  - consistent `{ requestId?, action?, ok:false, error:{code,message,details} }`
- [ ] Implement `X-Request-Id` echoing:
  - if header present, echo in response header
  - if both header and body `requestId` present they MUST match else `400 request_id_mismatch`
- [ ] Implement `Idempotency-Key` vs body `idempotencyKey` mismatch:
  - `400 invalid_idempotency_key`
- [ ] Publish an in-code error registry (enum/consts) aligned to `openapi-v2.skeleton.yaml`’s seed list.

Exit criteria:
- For `/v2/actions`, every failure path returns a canonical error envelope (including `401` and rate limiting).
- OpenAPI documents those non-2xx responses with schemas.

---

## Phase 3 — Idempotency (SQLite-backed) + in-flight dedupe

Goal: safe retries for state-changing v2 actions.

Dependencies: Phase 2.

Tasks:
- [ ] Add SQLite table(s) for idempotency:
  - key fields: `credential_fingerprint`, `idempotency_key`, `action`, `request_hash`
  - state fields: `status` (in_progress|completed), `response_status_code`, `response_json`, timestamps, `expires_at`
  - indexes for lookup and TTL cleanup
- [ ] Implement idempotency wrapper for state-changing actions:
  - determine “state-changing” action allowlist (at minimum: `light.set`, `grouped_light.set`, `scene.activate`, `room.set`, `zone.set`)
  - compute stable request hash (canonical JSON of `{action,args}` + any relevant headers)
  - if existing completed record and hash matches → replay stored response (status + body)
  - if existing completed record and hash differs → `409 idempotency_key_reuse_mismatch`
  - if in-progress → `409 idempotency_in_progress` with retry guidance (`Retry-After` + `retryAfterMs`)
- [ ] Implement TTL cleanup job (background task):
  - default TTL 15 minutes
  - hard cap max rows (prevent unbounded growth)
- [ ] Add tests:
  - replay on duplicate request
  - mismatch error on key reuse with different payload
  - in-flight error includes retry guidance

Exit criteria:
- Re-sending a state change with the same idempotency key never causes double actuation.

---

## Phase 4 — Match engine v2 (per-request options + safe defaults)

Goal: deterministic, safe name resolution for state-changing commands.

Dependencies: Phase 2 (error model), Phase 3 (idempotency for state changes).

Tasks:
- [ ] Implement `MatchOptions` support on:
  - `resolve.by_name`
  - all name-accepting state-changing actions (`light.set`, `grouped_light.set`, `scene.activate`, `room.set`, `zone.set`)
- [ ] Implement match modes:
  - `exact`, `case_insensitive`, `normalized` (use `normalize_name = " ".join(value.strip().lower().split())`), `fuzzy`
- [ ] Implement minConfidence / minGap / maxCandidates semantics:
  - safe defaults for state-changing actions (minConfidence ~0.85, minGap ~0.15, maxCandidates 5–10)
  - `no_confident_match` vs `ambiguous_name` distinction:
    - no confident match: best score below threshold
    - ambiguous: multiple plausible matches without sufficient gap
- [ ] Ensure actions never “guess” a low-confidence target.
- [ ] Add tests covering:
  - exact / normalized matching
  - ambiguous_name candidate ordering + truncation
  - no_confident_match behavior for unrelated strings

Exit criteria:
- State-changing name-based actions cannot actuate without a confident match.

---

## Phase 5 — Verification engine + clamping + tolerances

Goal: a testable verify contract shared by all “set” actions.

Dependencies: Phase 2 (envelope), Phase 4 (target resolution).

Tasks:
- [ ] Implement state normalization + clamping:
  - build `requested` from input
  - build `applied` after:
    - basic clamp (brightness 0–100, colorTempK > 0)
    - capability clamp when available (use cached CLIP v2 resource schemas where possible)
  - emit `warnings[]` for clamped fields
- [ ] Implement verify modes:
  - `none`: skip verification
  - `poll`: GET the resource until observed matches `applied` within tolerances or timeout
  - `sse`: wait for cache update / SSE-driven observation
  - `poll_then_sse`: small poll window then wait on SSE
- [ ] Lock defaults (as per 0v91):
  - `on`: exact
  - brightness: `light.*` ±5; grouped/room/zone ±25
  - colorTempK: `light.*` ±200K; grouped/room/zone ±800K
  - `xy`: verify off by default unless explicitly requested
- [ ] Verification compares `observed` vs `applied` (post-clamp), not vs `requested`.
- [ ] Decide verify failure semantics:
  - recommended: still `ok:true` with `verified:false` + warnings (actuation happened; verification didn’t converge)
- [ ] Add tests (mock bridge) for:
  - verified true within tolerance
  - verified false on timeout + mismatch warning payload

Exit criteria:
- All state-changing “set” actions can return `{requested, applied, observed?, verified, warnings[]}` consistently.

---

## Phase 6 — Implement `/v2/actions` action catalog (ported v1 + v2 behavior)

Goal: v2 supports the full v1 action catalog, but with v2 envelopes/options.

Dependencies: Phases 2–5.

Tasks:
- [ ] Implement v2 handlers for existing actions:
  - `bridge.set_host` (same behavior; v2 envelope)
  - `bridge.pair` (same behavior; v2 envelope; keep `link_button_not_pressed`)
  - `clipv2.request` (same safety rules; v2 envelope)
  - `resolve.by_name` (v2 match options)
  - `light.set` (v2: match + verify + SetStateResult)
  - `grouped_light.set` (v2: match + verify + SetStateResult)
  - `scene.activate` (v2: match + idempotency; verify optional)
- [ ] Ensure error mappings match the v2 registry:
  - `bridge_unreachable` (424), `bridge_rate_limited` (429), `bridge_error` (502)
- [ ] Add tests for each v2 action:
  - request parsing via discriminator
  - canonical error envelopes on failures

Exit criteria:
- Every v1 action works via `/v2/actions` using the v2 contracts.

---

## Phase 7 — New v2 high-level actions: room.set / zone.set / inventory.snapshot

Goal: collapse multi-step client flows and reduce parsing/round-trips.

Dependencies: Phase 6 (core v2 actions), Phase 5 (verify engine).

### 7.1 room.set
Tasks:
- [ ] Resolve room by `roomRid` or `roomName` (match options apply to `roomName`).
- [ ] Extract `groupedLightRid` from the room resource (`services[].rtype == "grouped_light"`).
- [ ] Apply state to grouped light + verify (default verify enabled).
- [ ] Return `roomRid`, `groupedLightRid`, and SetStateResult (`requested/applied/observed/verified/warnings`).
- [ ] Tests for:
  - roomName success
  - missing grouped_light service → deterministic error (`not_found` or a new `invalid_room` code, if desired)

### 7.2 zone.set (dryRun + impact)
Tasks:
- [ ] Resolve zone by `zoneRid` or `zoneName`.
- [ ] Compute impact (best-effort):
  - `affectedRooms[]` (rid + optional name)
  - `affectedLightsCount`
- [ ] If `dryRun=true`, return impact only (no actuation).
- [ ] Else, set zone grouped light + verify + include impact.
- [ ] Tests for:
  - dryRun path
  - apply path with verify

### 7.3 inventory.snapshot (normalized read model + revision)
Tasks:
- [ ] Build normalized snapshot from cache/DB:
  - rooms `{rid,name,groupedLightRid}`
  - zones `{rid,name,groupedLightRid,roomRids?}` (best-effort)
  - lights `{rid,name,ownerDeviceRid,roomRid?}` (best-effort)
- [ ] Implement `revision` tracking:
  - monotonic integer
  - increments only on inventory-affecting changes (names/mappings/membership)
- [ ] Support `ifRevision`:
  - if unchanged → `{notModified:true, revision}`
- [ ] Implement `stale` + `staleReason`:
  - `not_configured|bridge_unreachable|sse_disconnected|cache_too_old|unknown`
- [ ] Tests for:
  - notModified flow
  - stale reasons (unit-level with injected state)

Exit criteria:
- RemcoChat can execute “room set” and “zone dryRun impact” in single calls and keep an inventory cache without raw CLIP parsing.

---

## Phase 8 — actions.batch (sequential + partial reporting)

Goal: reduce latency/failure surface for multi-step flows.

Dependencies: Phase 6 (v2 actions), Phase 7 (new actions for batching).

Tasks:
- [ ] Implement `actions.batch` request handling:
  - sequential execution
  - default `continueOnError=false` (stop on first error)
  - `continueOnError=true` returns `207` with per-step results
- [ ] Ensure per-step attribution (minimum):
  - index, action, effective requestId, effective idempotency key, status, ok, result/error
- [ ] Stop-on-error behavior:
  - return the failing step’s status code
  - canonical error envelope
  - include `failedStepIndex` and `steps[]` audit in `error.details`
- [ ] Tests:
  - continueOnError 207 shape
  - stopOnError returns failing status + audit

Exit criteria:
- Batch can replace common “resolve → fetch → set → verify” scripts when needed.

---

## Phase 9 — SSE v2: cursor resume + needs_resync + deltas

Goal: resumable, cache-friendly events with minimal polling.

Dependencies: Phase 7 (revision tracking), Phase 2 (canonical headers).

Tasks:
- [ ] Extend event hub to support:
  - monotonic event cursor (`id:`) per published event
  - replay buffer lookup by `Last-Event-ID`
- [ ] Implement `/v2/events/stream`:
  - emits `id:` and `event:` lines plus `data: <json>`
  - supports `Last-Event-ID`
  - if replay unavailable → emit `needs_resync`
  - includes `revision` in every JSON payload
- [ ] Emit useful deltas in `data` for:
  - `light` updates (on, brightness, colorTempK/xy when present)
  - `grouped_light` updates
- [ ] Optional non-breaking backport:
  - add `id:` frames to `/v1/events/stream` (additive)
- [ ] Tests:
  - SSE formatting and keepalive frames
  - resume success and needs_resync behavior

Exit criteria:
- RemcoChat can maintain a local cache without polling and can resume after disconnect.

---

## Phase 10 — Rate limiting v2 (actionable 429)

Goal: make 429 deterministic and client-actionable.

Dependencies: Phase 2 (envelope), Phase 3 (idempotency).

Tasks:
- [ ] Upgrade token bucket limiter to compute wait time:
  - return `retryAfterMs` and set `Retry-After` header when possible
- [ ] Ensure v2 429 responses:
  - canonical envelope
  - `error.code=rate_limited` (gateway) vs `bridge_rate_limited` (upstream)
  - `error.details.retryAfterMs` always present
- [ ] Tests:
  - gateway 429 includes headers + retryAfterMs
  - upstream 429 mapped to bridge_rate_limited

Exit criteria:
- Client can implement a single backoff strategy without guessing.

---

## Phase 11 — OpenAPI parity + docs

Goal: ensure runtime OpenAPI and the v2 skeleton stay aligned and codegen-ready.

Dependencies: Phase 6–10.

Tasks:
- [ ] Update FastAPI OpenAPI descriptions/examples to match `openapi-v2.skeleton.yaml`.
- [ ] Add a small “spec drift” check:
  - validate `/openapi.json` includes `/v2/*`
  - verify discriminator + schemas present
- [ ] Update README with v2 usage examples:
  - headers (auth, correlation, idempotency)
  - `room.set`, `zone.set` dryRun, `inventory.snapshot`, SSE resume example
- [ ] Keep `spec-v2.md` current with any implementation-driven clarifications.

Exit criteria:
- A TS client generator can produce types that discriminate v2 requests and parse canonical v2 errors.

---

## Phase 12 — Hardening + release checklist

Goal: production-ish robustness (LAN-only) without scope creep.

Dependencies: all previous phases.

Tasks:
- [ ] Ensure logs never include secrets (tokens, API keys, application key).
- [ ] Ensure idempotency store bounded + TTL cleanup verified.
- [ ] Validate behavior under bridge disconnect/reconnect:
  - staleReason transitions for inventory
  - SSE needs_resync emission on resume failures
- [ ] Final run: full test suite + smoke tests.

Exit criteria:
- v2 endpoints meet the locked spec decisions and RemcoChat can adopt them as the single programmatic client.

