import asyncio
import json

import pytest
from starlette.requests import Request


@pytest.fixture(autouse=True)
def _v2_sse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("GATEWAY_AUTH_TOKENS", "dev-token")
    monkeypatch.setenv("RATE_LIMIT_RPS", "1000")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1000")


def _mk_request(*, app, headers: dict[str, str]) -> Request:
    raw_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v2/events/stream",
        "headers": raw_headers,
        "app": app,
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, _receive)


@pytest.mark.asyncio
async def test_v2_sse_emits_id_and_event_payload():
    from hue_gateway.app import app, lifespan
    from hue_gateway.v2.router import v2_events_stream

    async with lifespan(app):
        await asyncio.sleep(0)
        req = _mk_request(app=app, headers={"Authorization": "Bearer dev-token"})
        resp = await v2_events_stream(req)

        await app.state.state.hub.publish(
            {
                "ts": "2026-02-04T00:00:00Z",
                "type": "resource.updated",
                "resource": {"rid": "1", "rtype": "light"},
                "data": {},
            }
        )

        first = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=3.0)  # type: ignore[attr-defined]
        if isinstance(first, bytes):
            first = first.decode("utf-8", "ignore")
        assert first.startswith("id: ")
        # Consume the data chunk too.
        second = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=3.0)  # type: ignore[attr-defined]
        if isinstance(second, bytes):
            second = second.decode("utf-8", "ignore")
        assert second.startswith("data: ")
        payload = json.loads(second[len("data: ") :].strip())
        assert payload["type"] == "resource.updated"
        assert "revision" in payload


@pytest.mark.asyncio
async def test_v2_sse_needs_resync_when_replay_unavailable():
    from hue_gateway.app import app, lifespan
    from hue_gateway.v2.router import v2_events_stream

    async with lifespan(app):
        await asyncio.sleep(0)
        req = _mk_request(app=app, headers={"Authorization": "Bearer dev-token", "Last-Event-ID": "999"})
        resp = await v2_events_stream(req)

        first = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=3.0)  # type: ignore[attr-defined]
        if isinstance(first, bytes):
            first = first.decode("utf-8", "ignore")
        assert first.startswith("id: ")
        second = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=3.0)  # type: ignore[attr-defined]
        if isinstance(second, bytes):
            second = second.decode("utf-8", "ignore")
        assert second.startswith("data: ")
        payload = json.loads(second[len("data: ") :].strip())
        assert payload["type"] == "needs_resync"
        assert payload["resource"] is None

