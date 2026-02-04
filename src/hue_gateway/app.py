from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import replace

import json
import logging
import time
from typing import Callable

from fastapi import Body, Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

from hue_gateway.actions import ActionDispatcher
from hue_gateway.cache import ResourceCache
from hue_gateway.config import AppConfig
from hue_gateway.db import Database
from hue_gateway.event_hub import EventHub
from hue_gateway.hue_client import HueClient
from hue_gateway.hue_client import HueTransportError, HueUpstreamError
from hue_gateway.hue_sync import resync_loop, sse_ingest_loop, sync_core_resources
from hue_gateway.openapi_custom import install_custom_openapi
from hue_gateway.rate_limit import TokenBucketLimiter
from hue_gateway.security import AuthContext, require_auth
from hue_gateway.schemas import (
    ActionRequest,
    ActionResponse,
    HealthResponse,
    RateLimitedResponse,
    ReadinessResponse,
    UnauthorizedResponse,
)


@dataclass
class AppState:
    config: AppConfig
    db: Database
    bridge_host: str | None
    application_key: str | None
    hue: HueClient
    dispatcher: ActionDispatcher
    cache: ResourceCache
    hub: EventHub
    v2_bus: "V2EventBus"
    limiter: TokenBucketLimiter
    tasks: list[asyncio.Task]


def _default_db_path() -> str:
    env = os.getenv("DB_PATH")
    if env:
        return env

    preferred_dir = "/data"
    try:
        if os.path.isdir(preferred_dir) and os.access(preferred_dir, os.W_OK):
            return os.path.join(preferred_dir, "hue-gateway.db")
    except OSError:
        pass

    return os.path.join(os.getcwd(), ".data", "hue-gateway.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AppConfig.from_env()
    db_path = _default_db_path()
    db = Database(db_path=db_path)
    await db.connect()

    bridge_host = config.bridge_host or await db.get_setting("bridge_host")
    application_key = config.application_key or await db.get_setting("application_key")
    config = replace(config, bridge_host=bridge_host, application_key=application_key)

    if config.bridge_host:
        await db.set_setting("bridge_host", config.bridge_host)
    if config.application_key:
        await db.set_setting("application_key", config.application_key)

    hue = HueClient(
        bridge_host=config.bridge_host,
        application_key=config.application_key,
    )
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    cache = ResourceCache()
    hub = EventHub()
    from hue_gateway.v2.event_bus import V2EventBus

    v2_bus = V2EventBus(replay_maxlen=500)
    limiter = TokenBucketLimiter(rate_per_sec=config.rate_limit_rps, burst=config.rate_limit_burst)

    tasks: list[asyncio.Task] = []

    app.state.state = AppState(
        config=config,
        db=db,
        bridge_host=config.bridge_host,
        application_key=config.application_key,
        hue=hue,
        dispatcher=dispatcher,
        cache=cache,
        hub=hub,
        v2_bus=v2_bus,
        limiter=limiter,
        tasks=tasks,
    )

    # Housekeeping for v2 idempotency records (no-op until v2 uses them).
    from hue_gateway.v2.idempotency import cleanup_loop as _idempotency_cleanup_loop

    tasks.append(asyncio.create_task(_idempotency_cleanup_loop(db=db)))

    # Feed v2 SSE from the existing bridge ingestion hub.
    from hue_gateway.v2.event_forwarder import forward_v1_to_v2_loop as _forward_v1_to_v2_loop

    tasks.append(asyncio.create_task(_forward_v1_to_v2_loop(db=db, cache=cache, hub=hub, bus=v2_bus)))

    async def bootstrap_loop() -> None:
        started = False
        while True:
            # Env (already in config) wins; DB is fallback.
            bridge_host_now = config.bridge_host or await db.get_setting("bridge_host")
            app_key_now = config.application_key or await db.get_setting("application_key")

            state: AppState = app.state.state
            if bridge_host_now != state.bridge_host or app_key_now != state.application_key:
                state.bridge_host = bridge_host_now
                state.application_key = app_key_now
                hue.configure(bridge_host=bridge_host_now, application_key=app_key_now)

            if not started and bridge_host_now and app_key_now:
                started = True
                state.tasks.append(asyncio.create_task(sync_core_resources(db=db, hue=hue, cache=cache)))
                state.tasks.append(
                    asyncio.create_task(
                        resync_loop(db=db, hue=hue, cache=cache, seconds=config.cache_resync_seconds)
                    )
                )
                state.tasks.append(
                    asyncio.create_task(
                        sse_ingest_loop(
                            db=db,
                            hue=hue,
                            cache=cache,
                            hub=hub,
                            resync_seconds=config.cache_resync_seconds,
                        )
                    )
                )

            await asyncio.sleep(2.0)

    tasks.append(asyncio.create_task(bootstrap_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except BaseException:
                pass
        await hue.close()
        await db.close()


app = FastAPI(
    title="Hue Gateway",
    version="0.1.0",
    description=(
        "# Hue Gateway API\n\n"
        "LAN-only Hue Bridge gateway for agentic tool calling.\n\n"
        "## Auth\n"
        "All `/v1/*` endpoints require **one** of:\n\n"
        "- `Authorization: Bearer <token>`\n"
        "- `X-API-Key: <key>`\n\n"
        "If auth is missing/invalid: **401** `{ \"detail\": {\"error\":\"unauthorized\"} }`.\n\n"
        "## Key concepts\n"
        "- The Hue Bridge is on your LAN and is addressed by `HUE_BRIDGE_HOST` (stored in gateway DB via `bridge.set_host`).\n"
        "- The Hue application key is created by pressing the physical bridge button and calling `bridge.pair`.\n"
        "- Most Hue v2 operations are available via `clipv2.request` pass-through.\n"
        "- For convenience, the gateway also provides high-level actions (`light.set`, `grouped_light.set`, `scene.activate`).\n\n"
        "## Endpoints\n"
        "- `GET /healthz` liveness\n"
        "- `GET /readyz` readiness (bridge host + app key + connectivity)\n"
        "- `POST /v1/actions` **single action endpoint** (typed request/response via `action` discriminator)\n"
        "- `GET /v1/events/stream` SSE stream of normalized events\n\n"
        "## `/v1/actions` cookbook\n"
        "All requests use the same envelope:\n\n"
        "```json\n"
        "{ \"requestId\": \"optional\", \"action\": \"...\", \"args\": { } }\n"
        "```\n\n"
        "### 1) Set the bridge host\n"
        "```json\n"
        "{ \"action\": \"bridge.set_host\", \"args\": { \"bridgeHost\": \"192.168.1.29\" } }\n"
        "```\n\n"
        "### 2) Pair (press the bridge button first)\n"
        "```json\n"
        "{ \"action\": \"bridge.pair\", \"args\": { \"devicetype\": \"hue-gateway#docker\" } }\n"
        "```\n"
        "If the button was not pressed recently: **409** with `error.code=link_button_not_pressed`.\n\n"
        "### 3) List rooms (CLIP v2 pass-through)\n"
        "```json\n"
        "{ \"action\": \"clipv2.request\", \"args\": { \"method\": \"GET\", \"path\": \"/clip/v2/resource/room\" } }\n"
        "```\n\n"
        "### 4) Turn off a room/zone (grouped light)\n"
        "Get the grouped light rid from the room resource (`services[].rtype == \"grouped_light\"`).\n"
        "```json\n"
        "{ \"action\": \"grouped_light.set\", \"args\": { \"rid\": \"<grouped_light_rid>\", \"on\": false } }\n"
        "```\n\n"
        "### 5) Turn on a light by name\n"
        "```json\n"
        "{ \"action\": \"light.set\", \"args\": { \"name\": \"Kitchen\", \"on\": true, \"brightness\": 30, \"colorTempK\": 2700 } }\n"
        "```\n"
        "If the name is ambiguous: **409** with `error.code=ambiguous_name` and a candidate list.\n\n"
        "### 6) Activate a scene\n"
        "```json\n"
        "{ \"action\": \"scene.activate\", \"args\": { \"name\": \"Relax\" } }\n"
        "```\n\n"
        "## Common errors\n"
        "- **400** invalid JSON / invalid args / unknown action (gateway returns a standard error envelope)\n"
        "- **409** link button not pressed, or ambiguous name resolution\n"
        "- **424** bridge unreachable (network/connectivity)\n"
        "- **429** gateway rate limited: `{ \"error\": \"rate_limited\" }`\n"
        "- **502** bridge returned a non-2xx error\n\n"
        "## Events (SSE)\n"
        "`GET /v1/events/stream` returns `text/event-stream`.\n\n"
        "Clients should keep the connection open and reconnect on disconnect. The gateway may send keepalive\n"
        "comment frames (`: keepalive`). Each event is emitted as a single `data: <json>` frame.\n"
    ),
    lifespan=lifespan,
)
install_custom_openapi(app)

logger = logging.getLogger("hue_gateway")

# v2 routes are implemented in a dedicated module to keep /v1 stable.
from hue_gateway.v2.router import router as v2_router  # noqa: E402

app.include_router(v2_router)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Normalize validation errors into stable envelopes.
    path = request.url.path or ""
    is_v2 = path.startswith("/v2/")
    request_id_header = request.headers.get("x-request-id")

    def _json_safe(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", "ignore")
            except Exception:
                return repr(value)
        if isinstance(value, list):
            return [_json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [_json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        return str(value)

    details = {"errors": _json_safe(exc.errors())}
    # If the body isn't valid JSON, FastAPI raises a validation error too. We surface it as invalid_json.
    code = "invalid_request"
    message = "Request validation failed"
    for err in exc.errors():
        if err.get("type") == "json_invalid":
            code = "invalid_json"
            message = "Request body must be valid JSON"
            details = {"error": str(err.get("msg", "invalid json"))}
            break
        if err.get("type") == "model_attributes_type" and err.get("loc") == ("body",):
            # Typically means the body wasn't a JSON object (or Content-Type wasn't application/json).
            code = "invalid_json"
            message = "Request body must be a JSON object"
            details = {"error": str(err.get("msg", "invalid body"))}
            break

    if is_v2 and code == "invalid_request":
        # Best-effort specialization into v2's more specific error codes.
        # Prefer unknown_action when the body action is a string we don't recognize.
        # (Pydantic's error type here isn't always a discriminator-specific tag error.)
        try:
            raw = await request.body()
            parsed = json.loads(raw) if raw else None
            body_action = parsed.get("action") if isinstance(parsed, dict) else None
            if isinstance(body_action, str):
                known_actions = {
                    "bridge.set_host",
                    "bridge.pair",
                    "clipv2.request",
                    "resolve.by_name",
                    "light.set",
                    "grouped_light.set",
                    "scene.activate",
                    "room.set",
                    "zone.set",
                    "inventory.snapshot",
                    "actions.batch",
                }
                if body_action not in known_actions:
                    code = "unknown_action"
                    message = "Unknown action"
        except Exception:
            pass

        if code == "invalid_request":
            for err in exc.errors():
                loc = err.get("loc")
                if loc == ("body", "action"):
                    code = "invalid_action"
                    message = "Field 'action' must be a valid action string"
                    break
                if isinstance(loc, tuple) and len(loc) >= 2 and loc[0] == "body" and loc[1] == "args":
                    code = "invalid_args"
                    message = "Field 'args' must match the action schema"
                    break

    # v2 prefers echoing `action`/`requestId` from the body when parseable, but the header wins for correlation.
    body_action: str | None = None
    body_request_id: str | None = None
    if is_v2 and code != "invalid_json":
        try:
            raw = await request.body()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("action"), str):
                        body_action = parsed["action"]
                    if isinstance(parsed.get("requestId"), str):
                        body_request_id = parsed["requestId"]
        except Exception:
            pass

    response_request_id = request_id_header if request_id_header else body_request_id
    response_action = body_action if is_v2 else ""
    payload = (
        {
            "requestId": response_request_id,
            "action": response_action,
            "ok": False,
            "error": {"code": code, "message": message, "details": details},
        }
        if is_v2
        else {
            "requestId": request_id_header,
            "action": "",
            "ok": False,
            "error": {"code": code, "message": message, "details": details},
        }
    )
    headers = {}
    if is_v2 and request_id_header:
        headers["X-Request-Id"] = request_id_header
    return JSONResponse(payload, status_code=status.HTTP_400_BAD_REQUEST, headers=headers)


@app.middleware("http")
async def access_log(request: Request, call_next: Callable[[Request], Response]):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    request_id = request.headers.get("x-request-id", "")
    logger.info(
        "%s %s -> %s (%.1fms) rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


@app.get(
    "/healthz",
    summary="Liveness check",
    description="Returns `ok=true` if the process is alive.",
    response_model=HealthResponse,
    tags=["meta"],
)
async def healthz() -> HealthResponse:
    return {"ok": True}


@app.get(
    "/readyz",
    summary="Readiness check",
    description=(
        "Returns `ready=true` when the gateway has a bridge host + application key and can reach the Hue Bridge.\n\n"
        "Common not-ready reasons:\n"
        "- `missing_bridge_host`\n"
        "- `missing_application_key`\n"
        "- `bridge_unreachable`\n"
        "- `bridge_error`\n"
    ),
    response_model=ReadinessResponse,
    tags=["meta"],
)
async def readyz() -> ReadinessResponse:
    state: AppState = app.state.state
    if not state.bridge_host:
        return JSONResponse({"ready": False, "reason": "missing_bridge_host"}, status_code=503)
    if not state.application_key:
        return JSONResponse({"ready": False, "reason": "missing_application_key"}, status_code=503)

    try:
        await state.dispatcher.hue.get_json("/clip/v2/resource/bridge")
    except HueTransportError as exc:
        return JSONResponse(
            {"ready": False, "reason": "bridge_unreachable", "details": str(exc)},
            status_code=503,
        )
    except HueUpstreamError as exc:
        return JSONResponse(
            {"ready": False, "reason": "bridge_error", "details": {"status": exc.status_code}},
            status_code=503,
        )
    return {"ready": True}


@app.post(
    "/v1/actions",
    summary="Single action endpoint",
    description=(
        "Execute one action.\n\n"
        "This endpoint is intentionally *generic* for LLM tool calling. The `action` string selects "
        "the operation, and `args` carries action-specific parameters.\n\n"
        "Supported actions (v1):\n\n"
        "- `bridge.set_host`: persist the Hue Bridge host/IP in the gateway.\n"
        "  - args: `{ \"bridgeHost\": \"192.168.1.29\" }`\n\n"
        "- `bridge.pair`: create/store a Hue application key (press the bridge button first).\n"
        "  - args: `{ \"devicetype\": \"hue-gateway#docker\" }` (optional)\n"
        "  - on success result: `{ \"applicationKey\": \"...\", \"stored\": true }`\n"
        "  - if button not pressed: `409` with `error.code=link_button_not_pressed`\n\n"
        "- `clipv2.request`: CLIP v2 pass-through for advanced use.\n"
        "  - args: `{ \"method\": \"GET\", \"path\": \"/clip/v2/resource/room\", \"body\": {..} }`\n"
        "  - safety: `path` must start with `/clip/v2/` and cannot override host.\n\n"
        "- `resolve.by_name`: fuzzy name → rid resolution using cached/indexed names.\n"
        "  - args: `{ \"rtype\": \"light\", \"name\": \"Kitchen\" }`\n"
        "  - may return `409` `ambiguous_name` with candidates.\n\n"
        "- `light.set`: control a light by `rid` or fuzzy `name`.\n"
        "  - args include any of: `on`, `brightness` (0-100), `colorTempK`, `xy`.\n\n"
        "- `grouped_light.set`: control a room/zone grouped light by `rid` or fuzzy `name`.\n\n"
        "- `scene.activate`: activate a scene by `rid` or fuzzy `name`.\n\n"
        "Notes:\n"
        "- Some actions depend on the Hue Bridge inventory and device capabilities.\n"
        "- Rate limiting: `429` returns `{ \"error\": \"rate_limited\" }`.\n"
    ),
    response_model=ActionResponse,
    responses={
        200: {
            "description": "Action executed (or failed) using the standard action envelope.",
            "content": {
                "application/json": {
                    "examples": {
                        "success_example": {
                            "summary": "Successful action response",
                            "value": {
                                "requestId": "req-123",
                                "action": "clipv2.request",
                                "ok": True,
                                "result": {"status": 200, "body": {"data": []}},
                            },
                        },
                        "failure_example": {
                            "summary": "Failed action response",
                            "value": {
                                "requestId": "req-123",
                                "action": "resolve.by_name",
                                "ok": False,
                                "error": {
                                    "code": "ambiguous_name",
                                    "message": "Multiple matches for light name",
                                    "details": {"candidates": [{"rid": "...", "name": "Kitchen", "confidence": 0.92}]},
                                },
                            },
                        },
                    }
                }
            },
        },
        400: {"description": "Bad request / invalid JSON / unknown action / invalid args."},
        401: {
            "description": "Unauthorized (missing/invalid gateway auth).",
            "model": UnauthorizedResponse,
        },
        409: {"description": "Conflict (e.g., link button not pressed, ambiguous name)."},
        424: {"description": "Failed dependency (bridge unreachable)."},
        429: {"description": "Rate limited (gateway).", "model": RateLimitedResponse},
        502: {"description": "Bad gateway (bridge returned error)."},
        500: {"description": "Internal server error."},
    },
    tags=["actions"],
)
async def actions(
    request: Request,
    payload: ActionRequest = Body(
        ...,
        openapi_examples={
            "bridge_set_host": {
                "summary": "Set bridge host (store in gateway)",
                "value": {"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            },
            "bridge_pair": {
                "summary": "Pair after pressing the Hue Bridge button",
                "value": {"action": "bridge.pair", "args": {"devicetype": "hue-gateway#docker"}},
            },
            "list_rooms": {
                "summary": "List rooms via CLIP v2 pass-through",
                "value": {
                    "action": "clipv2.request",
                    "args": {"method": "GET", "path": "/clip/v2/resource/room"},
                },
            },
            "turn_off_grouped_light": {
                "summary": "Turn off a room/zone grouped light (by rid)",
                "value": {"action": "grouped_light.set", "args": {"rid": "<rid>", "on": False}},
            },
        },
    ),
    auth: AuthContext = Depends(require_auth),
) -> ActionResponse:
    # `ActionRequest` is a discriminated union; this is one concrete model at runtime.
    payload_dict = payload.model_dump()

    state: AppState = app.state.state
    if not state.limiter.allow(auth.credential):
        return JSONResponse({"error": "rate_limited"}, status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    response = await state.dispatcher.dispatch(payload=payload_dict, auth=auth)
    return JSONResponse(response.body, status_code=response.status_code)


@app.get(
    "/v1/events/stream",
    summary="Normalized Hue event stream (SSE)",
    description=(
        "Server-Sent Events (SSE) stream of normalized Hue change events.\n\n"
        "Response is `text/event-stream` where each event is sent as a single `data: <json>` frame.\n"
        "The gateway may also send `: keepalive` comment frames.\n\n"
        "Clients should:\n"
        "- keep the HTTP connection open\n"
        "- reconnect on disconnect\n"
        "- treat event `data` as best-effort (shape may evolve)\n"
    ),
    tags=["events"],
    responses={
        200: {
            "description": "SSE stream (text/event-stream).",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        401: {"description": "Unauthorized.", "model": UnauthorizedResponse},
    },
)
async def events_stream(_: AuthContext = Depends(require_auth)):
    state: AppState = app.state.state
    subscription = await state.hub.subscribe()

    async def _gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(subscription.queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, separators=(',',':'))}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await subscription.unsubscribe()

    return StreamingResponse(_gen(), media_type="text/event-stream")
