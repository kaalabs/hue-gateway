# Research: Dockerized Hue Gateway API (for agentic “skill” use)

## Goal
Build a small, dockerized API server (“Hue Gateway”) that:

- Runs on the same network as the Philips Hue Bridge (LAN).
- Exposes a stable, LLM-friendly API for an agentic chat server to call as a “skill”.
- Translates those calls into Philips Hue Bridge API requests (primarily Hue API v2 / CLIP v2).
- Handles first-time setup (bridge discovery + pairing to obtain an application key) and day-2 operations (control, status, scenes, groups).

## Non-goals (initially)
- Hue Entertainment streaming (low-latency UDP / sync).
- Multi-tenant cloud-hosted public service exposed to the internet.
- Full pass-through of every Hue API endpoint (keep a curated surface area first).

---

## Philips Hue control options (what exists)

### 1) Local Bridge API (recommended for LAN control)
Philips Hue is controlled via the Hue Bridge on the local network. The “Get Started” guide describes:

- Discovering the Bridge IP (including a broker endpoint).
- Using the Bridge’s debug tool at `https://<bridge ip>/debug/clip.html`.
- Creating an authorized user / application key by `POST /api` with `{"devicetype":"..."}`
  and pressing the physical link button on the Bridge when prompted.  
  Source: Philips Hue Developer Program “Get Started”.  
  https://developers.meethue.com/develop/get-started-2/

Bridge discovery broker endpoint mentioned there:

- https://discovery.meethue.com

### 2) Hue Remote API (cloud / OAuth)
For control outside the LAN without a VPN, Hue offers a cloud-based remote API using OAuth. This is heavier (account linking, token lifecycle) and usually unnecessary if your chat agent runs on the same LAN or can VPN into it. Keep as a future option.

---

## What’s “Hue API v2 / CLIP v2” in practice

### HTTPS-only + discovery changes (v2-era guidance)
Philips Hue announced with the v2 API rollout that:
- HTTP has been replaced by HTTPS (v2 API does not support HTTP).
- UPnP discovery was deprecated (recommendations include mDNS and `discovery.meethue.com`).  
Source: “New Hue API” announcement.  
https://developers.meethue.com/new-hue-api/

### Authentication / keys
- You must obtain an “application key” (historically “username”) by registering with the Bridge (requires pressing the physical button).
- Many integrations treat this as mandatory configuration and/or support generating it during a pairing flow.  
  Example: openHAB Hue binding v2 docs discuss `applicationKey` and note button-based generation.  
  https://www.openhab.org/addons/bindings/hue/doc/readme_v2.html

### HTTPS + self-signed certificates
- Hue API v2 typically uses HTTPS to the Bridge, with a self-signed certificate by default; clients often need an “allow self-signed” toggle or certificate pinning.  
  openHAB’s Hue v2 docs include a `useSelfSignedCertificate` option.  
  https://www.openhab.org/addons/bindings/hue/doc/readme_v2.html

Practical implication for the gateway:
- In development, most implementations set TLS verification off for Bridge calls.
- In production, prefer certificate pinning (store SHA256 fingerprint) or store/trust the Bridge certificate chain explicitly.

### Resource model (v2)
Hue API v2 uses “resources” (UUID-like IDs). You typically:
- `GET /clip/v2/resource/<type>` to list resources.
- `PUT /clip/v2/resource/light/<id>` to update a light.
- `PUT /clip/v2/resource/grouped_light/<id>` to update a room/zone “grouped light”.

Reference payload examples for v2 light updates (on/dimming/color temperature/xy color) can be found in community and wrapper docs, e.g.:
- OpenHue light resource: https://www.openhue.io/api/openhue-api/light
- OpenHue grouped light resource: https://www.openhue.io/api/openhue-api/grouped-light

### Event streaming (optional but valuable)
Hue API v2 supports an event stream (Server-Sent Events) endpoint for real-time changes. This is commonly used by home automation stacks.  
openHAB’s Hue v2 docs mention SSE/event stream connections.  
https://www.openhab.org/addons/bindings/hue/doc/readme_v2.html

The endpoint is commonly accessed like:
- `GET https://<bridge ip>/eventstream/clip/v2`
- Headers: `hue-application-key: <appkey>` and `Accept: text/event-stream`
- Typically `--insecure` is required unless you trust/pin the Bridge certificate.  
Example (community forum):  
https://discourse.nodered.org/t/converting-curl-event-stream-to-node-red/76506

Gateway implication:
- You can keep local state “warm” (cache of names/ids + last known state) and provide fast lookups for the agent.
- You can optionally expose a simplified SSE endpoint to agent clients.

---

## Why a gateway service (instead of direct Hue API calls from the agent)

Problems with calling the Bridge directly from an agentic chat server:
- Network locality: the Bridge is normally reachable only inside the LAN.
- TLS quirks: self-signed certificates and/or HTTP/2/SSE details.
- “Pairing” is interactive (requires physical button press).
- Hue API v2 has a large surface area and UUID resource model, which is awkward for LLM tool use.

Gateway benefits:
- A small, stable “skill API” that the LLM can call reliably (OpenAPI schema).
- Centralized configuration for Bridge IP + application key.
- Higher-level operations by *name* (e.g., “Kitchen lights to 30% warm white”) while still allowing ID-based operations.
- Safety controls (allowlists, rate limits, dry-run mode).

---

## Dockerization constraints & patterns

### Networking
- The container must be able to reach the Bridge IP on the LAN.
- Bridge discovery via mDNS/SSDP may not work reliably in Docker Desktop (macOS/Windows) due to multicast limitations; the broker endpoint `https://discovery.meethue.com` or manual IP configuration is often simplest.

Recommended approach:
- Support explicit config (`HUE_BRIDGE_HOST`) as the primary path.
- Provide a discovery endpoint that calls the broker (`https://discovery.meethue.com`) as a best-effort fallback.

### Secrets & persistence
You generally want to persist:
- Bridge host/IP (or discovered bridge id → IP mapping).
- Application key.
- Optional pinned TLS fingerprint.

Patterns:
- Mount a small volume (e.g. `/data/config.json`) and keep secrets there (with file permissions).
- Or inject secrets at runtime (`HUE_APPLICATION_KEY`) and avoid disk persistence.

### Operational endpoints
For orchestration and debugging:
- `GET /healthz` (process alive)
- `GET /readyz` (has valid Bridge config and can reach it)

---

## Proposed “Hue Gateway” service API (LLM-friendly)

Design goals:
- Keep the surface area small and semantically stable.
- Provide both *ID-based* and *name-based* access.
- Always return structured, tool-friendly JSON (no HTML).

### Core endpoints

**Meta**
- `GET /healthz` → `{ "ok": true }`
- `GET /readyz` → `{ "ready": true, "bridge": {...} }`
- `GET /v1/info` → version/build info

**Bridge setup**
- `GET /v1/bridges/discover` → calls broker discovery; returns `[ { id, internalipaddress, ... } ]`
- `POST /v1/bridges/config` → set bridge host (and optionally port)
- `POST /v1/bridges/pair` → attempt user/app-key creation; requires button press
  - request: `{ "devicetype": "hue-gateway#<hostname>" }`
  - response: `{ "applicationKey": "...", "stored": true }`

**Inventory**
- `GET /v1/lights` → list with id, name, capabilities, last known state
- `GET /v1/rooms` and `GET /v1/zones` → list grouped controls
- `GET /v1/scenes` → list scenes (optionally filtered by room/zone)

**Control (ID-based)**
- `PUT /v1/lights/{id}/state`
  - body (example): `{ "on": true, "brightness": 30, "colorTempK": 2700 }`
- `PUT /v1/grouped-lights/{id}/state` (rooms/zones)
- `POST /v1/scenes/{id}/activate`

**Control (name-based, for the LLM)**
- `POST /v1/actions`
  - body example:
    ```json
    {
      "target": { "type": "light", "name": "Kitchen" },
      "state": { "on": true, "brightness": 30, "colorTempK": 2700 }
    }
    ```
  - server behavior:
    - resolve name → id (strict, or fuzzy with confidence threshold + explicit ambiguity errors)
    - apply state
    - return `{ "matched": {...}, "result": {...} }`

**Events (optional)**
- `GET /v1/events/stream` → simplified SSE from Bridge event stream, with server-side filtering

### Input normalization
To keep the LLM interface simple:
- Brightness: accept 0–100 (%) and map to Hue v2 `dimming.brightness` (also 0–100).
- Color temperature:
  - accept Kelvin (`colorTempK`) and convert to Mirek (`mirek = round(1_000_000 / K)`), clamped to device range.
  - OR accept Mirek directly (`mirek`) for advanced usage.
- Color:
  - accept XY (`{x,y}`) directly for predictable control.
  - optionally accept CSS hex and convert to XY (needs gamut mapping; can be a later enhancement).

### Error model (important for tool reliability)
Standardize errors:
- `400` invalid inputs
- `401/403` missing or invalid gateway auth
- `409` ambiguous name match (include candidates)
- `424` Bridge unreachable / misconfigured (failed dependency)
- `429` rate-limited by Bridge (back off; avoid retry loops)
- `502` Bridge returned error; include sanitized details

---

## Security model for the gateway

### Don’t expose it directly to the internet
Recommended deployment:
- Run the gateway on a home server, NAS, or small machine (or the same host running the agent).
- Keep it bound to LAN interfaces only.
- If remote access is needed, use a VPN (e.g., Tailscale/WireGuard) or an authenticated reverse proxy.

### Gateway authentication
Even on a LAN, require an auth token for the gateway API:
- `Authorization: Bearer <token>` (static token initially; rotateable)
- Or `X-API-Key: ...`

### Bridge credential safety
- Treat the application key like a password.
- Store it in a secret store or in a volume with strict permissions.
- Avoid logging it.

---

## Implementation choices (language/framework)

### Strong default for “skill API”: FastAPI (Python)
Pros:
- Auto-generated OpenAPI schema for tool integration.
- Pydantic validation for strict inputs (helps LLM tool calls).
- Easy SSE proxy endpoints.
Cons:
- Need to handle TLS verification/pinning manually.

### Alternative: Node.js (Express/Fastify)
Pros:
- Familiar HTTP client/TLS handling and good ecosystem.
- Many Hue community libs exist (but evaluate maintenance).
Cons:
- OpenAPI generation is an extra step unless using a framework that generates it.

Recommendation for v1:
- Use FastAPI to get OpenAPI “for free”.
- Use a thin internal Hue client module that:
  - sets `hue-application-key` header
  - makes HTTPS requests to `https://<bridge>/clip/v2/...`
  - optionally disables cert verification or pins fingerprint

---

## Docker deliverables (what to build next)

### Minimal Dockerfile
- Slim base image (e.g., `python:3.12-slim`).
- Non-root user.
- `HEALTHCHECK` on `/healthz`.
- Config volume at `/data`.

### docker-compose.yml (example shape)
- `hue-gateway` service exposing a single port on the LAN host.
- Volume for `/data`.
- Env vars for bridge config and gateway auth token.

---

## Open questions / decisions for the spec phase

1) **Pairing flow**: store the application key automatically, or return it and require manual storage?
2) **TLS handling**: allow `INSECURE_SKIP_VERIFY=true` only for dev, and support fingerprint pinning for prod?
3) **Name resolution policy**: strict exact match only, or fuzzy matching with ambiguity errors?
4) **Scope**: lights only initially, or include rooms/zones/scenes from day one?
5) **Event stream**: include SSE support in v1, or add later?

---

## References (starting set)
- Philips Hue Developer Program: Get Started (bridge discovery + user creation)  
  https://developers.meethue.com/develop/get-started-2/
- Philips Hue Developer Program: New Hue API (HTTPS-only, UPnP deprecation)  
  https://developers.meethue.com/new-hue-api/
- Bridge discovery broker endpoint  
  https://discovery.meethue.com
- openHAB Hue binding (API v2 notes: application key, self-signed cert, SSE)  
  https://www.openhab.org/addons/bindings/hue/doc/readme_v2.html
- Node-RED forum thread with `eventstream/clip/v2` curl example (SSE endpoint shape)  
  https://discourse.nodered.org/t/converting-curl-event-stream-to-node-red/76506
- OpenHue (community docs; useful payload examples for v2 resources)  
  https://www.openhue.io/api/openhue-api/light  
  https://www.openhue.io/api/openhue-api/grouped-light
