from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Body, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from hue_gateway.rate_limit import TokenBucketLimiter
from hue_gateway.v2.dispatcher import V2Dispatcher
from hue_gateway.v2.schemas import (
    V2ActionError,
    V2ActionRequest,
    V2ActionSuccessResponse,
    V2ErrorEnvelope,
)
from hue_gateway.v2.security import authenticate_v2


router = APIRouter(prefix="/v2", tags=["v2"])


def _err(
    *,
    status_code: int,
    x_request_id: str | None,
    request_id: str | None,
    action: str | None,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = V2ErrorEnvelope(
        requestId=request_id,
        action=action,
        ok=False,
        error=V2ActionError(code=code, message=message, details=details or {}),
    ).model_dump(mode="json")
    headers = {}
    if x_request_id:
        headers["X-Request-Id"] = x_request_id
    return JSONResponse(body, status_code=status_code, headers=headers)


@router.post(
    "/actions",
    summary="Single action endpoint (v2)",
    responses={
        200: {"model": V2ActionSuccessResponse},
        207: {"model": V2ActionSuccessResponse},
        400: {"model": V2ErrorEnvelope},
        401: {"model": V2ErrorEnvelope},
        404: {"model": V2ErrorEnvelope},
        409: {"model": V2ErrorEnvelope},
        424: {"model": V2ErrorEnvelope},
        429: {"model": V2ErrorEnvelope},
        500: {"model": V2ErrorEnvelope},
        502: {"model": V2ErrorEnvelope},
    },
)
async def v2_actions(request: Request, payload: V2ActionRequest = Body(...)):
    x_request_id = request.headers.get("x-request-id")
    idempotency_key_header = request.headers.get("idempotency-key")

    if x_request_id and payload.requestId and x_request_id != payload.requestId:
        return _err(
            status_code=status.HTTP_400_BAD_REQUEST,
            x_request_id=x_request_id,
            request_id=x_request_id,
            action=payload.action,
            code="request_id_mismatch",
            message="X-Request-Id must match body requestId when both are present",
            details={"xRequestId": x_request_id, "requestId": payload.requestId},
        )
    effective_request_id = x_request_id or payload.requestId

    if idempotency_key_header and payload.idempotencyKey and idempotency_key_header != payload.idempotencyKey:
        return _err(
            status_code=status.HTTP_400_BAD_REQUEST,
            x_request_id=x_request_id,
            request_id=effective_request_id,
            action=payload.action,
            code="invalid_idempotency_key",
            message="Idempotency-Key must match body idempotencyKey when both are present",
            details={"idempotencyKeyHeader": idempotency_key_header, "idempotencyKeyBody": payload.idempotencyKey},
        )
    effective_idempotency_key = idempotency_key_header or payload.idempotencyKey

    auth = authenticate_v2(request)
    if not auth:
        return _err(
            status_code=status.HTTP_401_UNAUTHORIZED,
            x_request_id=x_request_id,
            request_id=effective_request_id,
            action=payload.action,
            code="unauthorized",
            message="Missing or invalid credentials",
        )

    state = request.app.state.state
    limiter: TokenBucketLimiter = state.limiter

    allowed, retry_after_ms = limiter.allow_with_retry_after_ms(auth.credential)
    if not allowed:
        headers = {}
        if x_request_id:
            headers["X-Request-Id"] = x_request_id
        if retry_after_ms > 0:
            headers["Retry-After"] = str(max(1, int((retry_after_ms + 999) / 1000)))
        body = V2ErrorEnvelope(
            requestId=effective_request_id,
            action=payload.action,
            ok=False,
            error=V2ActionError(
                code="rate_limited",
                message="Rate limited",
                details={"retryAfterMs": retry_after_ms},
            ),
        ).model_dump(mode="json")
        return JSONResponse(body, status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers=headers)

    action = payload.action
    request_id = effective_request_id

    v2_dispatcher = V2Dispatcher(db=state.db, hue=state.hue, cache=state.cache, config=state.config)
    resp = await v2_dispatcher.dispatch(
        payload=payload,
        auth=auth,
        request_id=request_id,
        idempotency_key=effective_idempotency_key,
    )
    headers: dict[str, str] = {}
    if x_request_id:
        headers["X-Request-Id"] = x_request_id
    if resp.headers:
        headers.update(resp.headers)
    return JSONResponse(resp.body, status_code=resp.status_code, headers=headers or None)


@router.get(
    "/events/stream",
    summary="Event stream (v2, stub)",
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        401: {"model": V2ErrorEnvelope},
    },
)
async def v2_events_stream(request: Request):
    auth = authenticate_v2(request)
    if not auth:
        return _err(
            status_code=401,
            x_request_id=request.headers.get("x-request-id"),
            request_id=request.headers.get("x-request-id"),
            action=None,
            code="unauthorized",
            message="Missing or invalid credentials",
        )

    state = request.app.state.state
    bus = state.v2_bus

    last_event_id = request.headers.get("last-event-id")
    last_cursor: int | None = None
    if last_event_id:
        try:
            last_cursor = int(last_event_id)
        except ValueError:
            last_cursor = None

    subscription = await bus.subscribe()

    def _now_ts() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    async def _gen():
        last_sent = 0
        try:
            if last_event_id:
                if last_cursor is None:
                    cursor = await bus.allocate_cursor()
                    revision = await state.db.get_setting_int("inventory_revision", default=0)
                    ev = {
                        "ts": _now_ts(),
                        "type": "needs_resync",
                        "resource": None,
                        "revision": int(revision),
                        "eventId": cursor,
                        "data": {"reason": "invalid_last_event_id", "lastEventId": last_event_id},
                    }
                    yield f"id: {cursor}\n"
                    yield f"data: {json.dumps(ev, separators=(',',':'))}\n\n"
                    last_sent = cursor
                else:
                    replay = await bus.replay_from(last_cursor)
                    if replay is None:
                        cursor = await bus.allocate_cursor()
                        revision = await state.db.get_setting_int("inventory_revision", default=0)
                        ev = {
                            "ts": _now_ts(),
                            "type": "needs_resync",
                            "resource": None,
                            "revision": int(revision),
                            "eventId": cursor,
                            "data": {"reason": "replay_unavailable", "lastEventId": last_event_id},
                        }
                        yield f"id: {cursor}\n"
                        yield f"data: {json.dumps(ev, separators=(',',':'))}\n\n"
                        last_sent = cursor
                    else:
                        for item in replay:
                            ev = dict(item.event)
                            ev["eventId"] = item.cursor
                            yield f"id: {item.cursor}\n"
                            yield f"data: {json.dumps(ev, separators=(',',':'))}\n\n"
                            last_sent = item.cursor

            while True:
                try:
                    item = await asyncio.wait_for(subscription.queue.get(), timeout=15.0)
                    if item.cursor <= last_sent:
                        continue
                    ev = dict(item.event)
                    ev["eventId"] = item.cursor
                    yield f"id: {item.cursor}\n"
                    yield f"data: {json.dumps(ev, separators=(',',':'))}\n\n"
                    last_sent = item.cursor
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await subscription.unsubscribe()

    headers = {}
    x_request_id = request.headers.get("x-request-id")
    if x_request_id:
        headers["X-Request-Id"] = x_request_id
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)
