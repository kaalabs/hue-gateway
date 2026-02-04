import httpx
import pytest


@pytest.fixture(autouse=True)
def _v2_batch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("GATEWAY_AUTH_TOKENS", "dev-token")
    monkeypatch.setenv("RATE_LIMIT_RPS", "1000")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1000")


@pytest.mark.asyncio
async def test_v2_actions_batch_stop_on_error_returns_error_envelope_with_audit():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"Authorization": "Bearer dev-token", "X-Request-Id": "r-b1"},
                json={
                    "requestId": "r-b1",
                    "action": "actions.batch",
                    "args": {
                        "actions": [
                            {"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
                            {"action": "resolve.by_name", "args": {"rtype": "light", "name": "x"}},
                        ]
                    },
                },
            )
            assert resp.status_code == 404
            body = resp.json()
            assert body["ok"] is False
            assert body["action"] == "actions.batch"
            assert body["error"]["code"] == "not_found"
            assert body["error"]["details"]["failedStepIndex"] == 1
            assert len(body["error"]["details"]["steps"]) == 2


@pytest.mark.asyncio
async def test_v2_actions_batch_continue_on_error_returns_207_success_envelope():
    from hue_gateway.app import app, lifespan

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v2/actions",
                headers={"Authorization": "Bearer dev-token", "X-Request-Id": "r-b2"},
                json={
                    "requestId": "r-b2",
                    "action": "actions.batch",
                    "args": {
                        "continueOnError": True,
                        "actions": [
                            {"action": "bridge.set_host", "args": {"bridgeHost": "192.168.1.29"}},
                            {"action": "resolve.by_name", "args": {"rtype": "light", "name": "x"}},
                        ],
                    },
                },
            )
            assert resp.status_code == 207
            body = resp.json()
            assert body["ok"] is True
            assert body["action"] == "actions.batch"
            assert body["result"]["continueOnError"] is True
            assert len(body["result"]["steps"]) == 2
            assert body["result"]["steps"][0]["ok"] is True
            assert body["result"]["steps"][1]["ok"] is False

