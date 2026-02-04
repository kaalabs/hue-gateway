import asyncio
import json

import httpx
import pytest


@pytest.fixture(autouse=True)
def _v1_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep v1 stable and hermetic: in-memory DB, deterministic auth, no bridge host/app key.
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("GATEWAY_AUTH_TOKENS", "dev-token")
    monkeypatch.setenv("GATEWAY_API_KEYS", "dev-key")
    monkeypatch.delenv("HUE_BRIDGE_HOST", raising=False)
    monkeypatch.delenv("HUE_APPLICATION_KEY", raising=False)


@pytest.mark.asyncio
async def test_v1_actions_shape_success_bridge_set_host():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/actions",
                headers={"Authorization": "Bearer dev-token"},
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert resp.status_code == 200
            body = resp.json()

            # Shape guard: v1 success envelope.
            assert set(body.keys()) == {"requestId", "action", "ok", "result"}
            assert body["requestId"] is None
            assert body["action"] == "bridge.set_host"
            assert body["ok"] is True
            assert set(body["result"].keys()) == {"bridgeHost", "stored"}
            assert body["result"]["bridgeHost"] == "192.168.1.29"
            assert body["result"]["stored"] is True


@pytest.mark.asyncio
async def test_v1_actions_shape_failure_dispatcher_error():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/actions",
                headers={"Authorization": "Bearer dev-token"},
                json={"requestId": "r1", "action": "resolve.by_name", "args": {"rtype": "light", "name": "x"}},
            )
            assert resp.status_code == 404
            body = resp.json()

            # Shape guard: v1 error envelope.
            assert set(body.keys()) == {"requestId", "action", "ok", "error"}
            assert body["requestId"] == "r1"
            assert body["action"] == "resolve.by_name"
            assert body["ok"] is False
            assert set(body["error"].keys()) == {"code", "message", "details"}
            assert body["error"]["code"] == "not_found"
            assert isinstance(body["error"]["message"], str)
            assert isinstance(body["error"]["details"], dict)


@pytest.mark.asyncio
async def test_v1_events_stream_shape_and_auth():
    from hue_gateway.app import app, lifespan
    from hue_gateway.security import AuthContext

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # v1 auth failure shape is FastAPI default HTTPException envelope.
            unauth = await client.get("/v1/events/stream")
            assert unauth.status_code == 401
            assert unauth.json() == {"detail": {"error": "unauthorized"}}

        # Avoid httpx streaming teardown quirks by consuming the endpoint generator directly.
        from hue_gateway.app import events_stream

        stream = await events_stream(AuthContext(credential="dev-token", scheme="bearer"))
        assert stream.media_type == "text/event-stream"

        await app.state.state.hub.publish({"type": "test", "value": 1})
        first = await asyncio.wait_for(stream.body_iterator.__anext__(), timeout=3.0)  # type: ignore[attr-defined]
        if isinstance(first, bytes):
            first = first.decode("utf-8", "ignore")
        assert first.startswith("data: ")
        payload = first[len("data: ") :].strip()
        assert json.loads(payload) == {"type": "test", "value": 1}
