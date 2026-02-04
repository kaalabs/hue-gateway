# Hue Gateway API v2.0 — semantic spec notes (companion to OpenAPI skeleton)

This document explains the key v2 contracts that are hard to fully express in OpenAPI schemas.

Primary source of truth: `docs/change_requests/hue-gateway-api-architecture-0v91.md`.

OpenAPI skeleton: `openapi-v2.skeleton.yaml`.

## 1) Versioning / compatibility

- `/v1/*` is behavior/shape-frozen for generated-client compatibility.
- v2 is delivered as new endpoints:
  - `POST /v2/actions`
  - `GET /v2/events/stream`

## 2) Canonical envelopes (v2)

- **Success (2xx)**: `ok:true` with typed `result`.
- **Failure (non-2xx)**: `ok:false` with the canonical error envelope:
  - `{ requestId?, action?, ok:false, error:{ code, message, details } }`

For `/v2/actions`:
- If `X-Request-Id` header and body `requestId` are both present they MUST match; otherwise `400 request_id_mismatch`.

## 3) Correlation vs idempotency (v2)

Correlation (logging/tracing):
- Header: `X-Request-Id` (preferred)
- Body: `requestId` (echoed)

Idempotency (replay-safe retries for state changes):
- Header: `Idempotency-Key` (preferred)
- Body: `idempotencyKey` (optional fallback)
- Header wins; if both are present and differ → `400 invalid_idempotency_key`.
- If key is already executing → `409 idempotency_in_progress` with retry guidance (`Retry-After` and/or `retryAfterMs`).

## 4) Verification contract

Verification compares `observed` against `applied` (post-clamping), not against `requested`.

Default tolerances (unless overridden via `verify.tolerances`):
- `light.*`:
  - brightness: ±5
  - colorTempK: ±200K
- `grouped_light` / `room.set` / `zone.set`:
  - brightness: ±25
  - colorTempK: ±800K
- `on`: exact match
- `xy`: verify off by default unless explicitly requested (best-effort verify is allowed when enabled)

Response fields for state-changing “set” actions:
- `requested` (client intent)
- `applied` (after capability clamping)
- `observed` (verification read, when verify enabled)
- `verified` (boolean)
- `warnings[]` (clamped fields, partial reachability, etc.)

## 5) Rate limiting

v2.0 behavior: **reject** with `429` (no internal queue).

Requirements:
- `Retry-After` header whenever computable
- `error.details.retryAfterMs` always present
- `error.code` differentiates:
  - `rate_limited` (gateway)
  - `bridge_rate_limited` (upstream)

Client guidance:
- Retry state changes only with idempotency keys; cap retries.

## 6) SSE: resume + inventory coherence

SSE event cursor (resume):
- Each SSE frame includes `id: <eventCursor>` (gateway-generated monotonic integer).
- Clients can resume using `Last-Event-ID: <eventCursor>`.
- If replay is not possible, the gateway emits `needs_resync`.

Inventory revision (refresh control):
- Each event JSON payload includes `revision` (gateway-generated monotonic integer).
- `revision` increments when the gateway observes an *inventory* change that would affect `inventory.snapshot` (names, mappings, inventory membership).
- Clients SHOULD refresh `inventory.snapshot` when `revision` changes.

Optional convenience:
- JSON payload may include `eventId` equal to the SSE `id:` (for clients that don’t surface SSE ids cleanly).

Minimum event payload:
- `{ ts, type, resource:{rid,rtype}|null, revision }`

For `light` and `grouped_light` updates:
- include a useful `data` delta to reduce polling.

## 7) inventory.snapshot

- Returns a normalized, stable inventory read model.
- Supports `ifRevision`:
  - if unchanged, returns `{ notModified:true, revision }` without the full payload.
- Includes `stale` and `staleReason` for UI messaging.

## 8) actions.batch

- Sequential execution.
- `continueOnError=false` (default):
  - stop at the first failing step and return that step’s status code.
  - error response remains canonical; batch audit data belongs in `error.details` (e.g., `failedStepIndex`, `steps[]`).
- `continueOnError=true`:
  - execute all steps and return `207` with per-step results.

