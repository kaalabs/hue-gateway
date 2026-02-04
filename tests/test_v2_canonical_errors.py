import httpx
import pytest


@pytest.fixture(autouse=True)
def _v2_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("GATEWAY_AUTH_TOKENS", "dev-token")
    monkeypatch.setenv("GATEWAY_API_KEYS", "dev-key")
    monkeypatch.setenv("RATE_LIMIT_RPS", "0")
    monkeypatch.setenv("RATE_LIMIT_BURST", "0")


@pytest.mark.asyncio
async def test_v2_actions_unauthorized_is_canonical():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"X-Request-Id": "r-1"},
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert resp.status_code == 401
            assert resp.headers.get("x-request-id") == "r-1"
            body = resp.json()
            assert body["ok"] is False
            assert body["requestId"] == "r-1"
            assert body["action"] == "bridge.set_host"
            assert body["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_v2_actions_request_id_mismatch():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"Authorization": "Bearer dev-token", "X-Request-Id": "a"},
                json={"requestId": "b", "action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert resp.status_code == 400
            body = resp.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "request_id_mismatch"


@pytest.mark.asyncio
async def test_v2_actions_idempotency_key_mismatch():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-2",
                    "Idempotency-Key": "h",
                },
                json={
                    "requestId": "r-2",
                    "idempotencyKey": "b",
                    "action": "bridge.set_host",
                    "args": {"bridgeHost": "192.168.1.29"},
                },
            )
            assert resp.status_code == 400
            body = resp.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "invalid_idempotency_key"


@pytest.mark.asyncio
async def test_v2_actions_unknown_action_is_unknown_action():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"Authorization": "Bearer dev-token", "X-Request-Id": "r-3"},
                json={"requestId": "r-3", "action": "nope", "args": {}},
            )
            assert resp.status_code == 400
            assert resp.headers.get("x-request-id") == "r-3"
            body = resp.json()
            assert body["ok"] is False
            assert body["requestId"] == "r-3"
            assert body["action"] == "nope"
            assert body["error"]["code"] == "unknown_action"


@pytest.mark.asyncio
async def test_v2_actions_rate_limited_is_canonical():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"Authorization": "Bearer dev-token", "X-Request-Id": "r-4"},
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert resp.status_code == 429
            body = resp.json()
            assert body["ok"] is False
            assert body["error"]["code"] == "rate_limited"
            assert "retryAfterMs" in body["error"]["details"]

