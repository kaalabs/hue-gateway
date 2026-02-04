import httpx
import pytest


@pytest.fixture(autouse=True)
def _v2_idempotency_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("GATEWAY_AUTH_TOKENS", "dev-token")
    monkeypatch.setenv("RATE_LIMIT_RPS", "1000")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1000")


@pytest.mark.asyncio
async def test_v2_idempotency_replay_overrides_request_id():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-1",
                    "Idempotency-Key": "k1",
                },
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert first.status_code == 200
            assert first.json()["requestId"] == "r-1"

            second = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-2",
                    "Idempotency-Key": "k1",
                },
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert second.status_code == 200
            body = second.json()
            assert body["requestId"] == "r-2"
            assert body["result"]["bridgeHost"] == "192.168.1.29"


@pytest.mark.asyncio
async def test_v2_idempotency_key_reuse_mismatch():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-1",
                    "Idempotency-Key": "k2",
                },
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert first.status_code == 200

            second = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-2",
                    "Idempotency-Key": "k2",
                },
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.30"}},
            )
            assert second.status_code == 409
            assert second.json()["error"]["code"] == "idempotency_key_reuse_mismatch"


@pytest.mark.asyncio
async def test_v2_idempotency_in_progress_returns_retry_guidance():
    from hue_gateway.app import app, lifespan
    from hue_gateway.security import AuthContext
    from hue_gateway.v2.idempotency import credential_fingerprint, mark_in_progress, request_hash

    async with lifespan(app):
        db = app.state.state.db
        auth = AuthContext(credential="dev-token", scheme="bearer")
        fp = credential_fingerprint(auth)
        req_hash = request_hash(action="bridge.set_host", args={"bridgeHost": "192.168.1.29"})
        await mark_in_progress(
            db=db,
            credential_fp=fp,
            key="k3",
            action="bridge.set_host",
            req_hash=req_hash,
            ttl_seconds=15 * 60,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={
                    "Authorization": "Bearer dev-token",
                    "X-Request-Id": "r-3",
                    "Idempotency-Key": "k3",
                },
                json={"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
            )
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"]["code"] == "idempotency_in_progress"
            assert "retryAfterMs" in body["error"]["details"]

