You are an engineer agent integrating with an already-running “Hue Gateway” Docker service. You may assume it is reachable and functioning; you are NOT responsible for deploying or operating it. Your job is to build an application integration that uses this gateway as the single interface to Philips Hue.

## Pragmatic connection details (current environment)
- Gateway base URL: `http://localhost:8000`
- Interactive docs: `http://localhost:8081`
- Auth (either works):
  - Bearer token: `Authorization: Bearer dev-token`
  - API key: `X-API-Key: dev-key`

## What this service is
- Hue Gateway is a LAN-only HTTP API that controls a Philips Hue Bridge (Hue API v2 / CLIP v2).
- It exposes a small set of high-level actions plus a safe CLIP v2 pass-through.
- It requires authentication on `/v1/*` endpoints.

## Start here (smoke tests)
1) Health:
- `GET http://localhost:8000/healthz` → `{ "ok": true }`

2) Readiness:
- `GET http://localhost:8000/readyz` → `{ "ready": true }`
  - If `ready=false`, do not proceed: the gateway is not paired/configured.

3) Fetch OpenAPI (generate a client from this):
- `GET http://localhost:8000/openapi.json`

4) List rooms (CLIP v2 pass-through):
- `POST http://localhost:8000/v1/actions`
  - Header: `Authorization: Bearer dev-token` (or `X-API-Key: dev-key`)
  - JSON:
    ```json
    { "action":"clipv2.request", "args": { "method":"GET", "path":"/clip/v2/resource/room" } }
    ```

## How to integrate (core contract)
- The integration is centered on `POST /v1/actions` with a single envelope:
  - Request:
    ```json
    { "requestId": "optional", "action": "<action>", "args": { } }
    ```
  - Success:
    ```json
    { "requestId": "optional", "action": "<action>", "ok": true, "result": { } }
    ```
  - Failure:
    ```json
    { "requestId": "optional", "action": "<action>", "ok": false, "error": { "code": "...", "message": "...", "details": { } } }
    ```
- The OpenAPI schema enumerates each supported action as a discriminated union by `action`. Generate types from it and treat it as the source of truth.

## Supported actions you’ll likely use
- `clipv2.request` (recommended “power tool” for listing and advanced ops)
  - args: `{ "method": "GET|POST|PUT|DELETE|HEAD|OPTIONS", "path": "/clip/v2/...", "body": {..optional..} }`
  - safety: `path` must start with `/clip/v2/` and cannot include scheme/host
- `light.set` (high-level)
  - args: `rid` or `name`, plus any of: `on`, `brightness` (0–100), `colorTempK`, `xy`
- `grouped_light.set` (high-level, rooms/zones)
  - args: `rid` or `name`, plus any of: `on`, `brightness`, `colorTempK`, `xy`
- `scene.activate`
  - args: `rid` or `name`
- `resolve.by_name`
  - args: `{ "rtype": "...", "name": "..." }` (may return 409 `ambiguous_name`)

## How to control a room (common pattern)
1) List rooms:
- `clipv2.request` GET `/clip/v2/resource/room`
2) In the room resource, find `services[]` where `rtype == "grouped_light"` and take `rid`.
3) Control that grouped light:
- `POST /v1/actions` with:
  ```json
  { "action":"grouped_light.set", "args": { "rid":"<grouped_light_rid>", "on": false } }
  ```

## Events (optional)
- `GET /v1/events/stream` (auth required) provides `text/event-stream`.
- Use SSE if your app needs near-real-time updates; otherwise polling via `clipv2.request` is acceptable for v1.

## Error handling (expected)
- 400: invalid request / invalid JSON / unknown action / invalid args
- 401: unauthorized
- 409: link button not pressed OR ambiguous name match
- 424: Hue Bridge unreachable
- 429: gateway rate-limited: `{ "error": "rate_limited" }`
- 502: Hue Bridge returned non-2xx error

## Deliverables you should produce
- Generated client/types from `http://localhost:8000/openapi.json`
- Integration module that centralizes auth headers and wraps the actions your app needs
- (Optional) SSE subscription helper for `/v1/events/stream` with reconnect logic

