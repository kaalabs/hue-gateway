import httpx
import pytest

from hue_gateway.actions import ActionDispatcher
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient
from hue_gateway.security import AuthContext


@pytest.mark.asyncio
async def test_clipv2_request_rejects_bad_path_prefix(config):
    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host="bridge.test", application_key="k", transport=httpx.MockTransport(lambda r: None))  # type: ignore[arg-type]
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={
                "requestId": "r1",
                "action": "clipv2.request",
                "args": {"method": "GET", "path": "/api/config"},
            },
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 400
        assert resp.body["error"]["code"] == "invalid_path"
    finally:
        await hue.close()
        await db.close()


@pytest.mark.asyncio
async def test_clipv2_request_rejects_host_override(config):
    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host="bridge.test", application_key="k", transport=httpx.MockTransport(lambda r: None))  # type: ignore[arg-type]
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={
                "requestId": "r2",
                "action": "clipv2.request",
                "args": {"method": "GET", "path": "https://evil/clip/v2/resource/light"},
            },
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 400
        assert resp.body["error"]["code"] == "invalid_path"
    finally:
        await hue.close()
        await db.close()


@pytest.mark.asyncio
async def test_clipv2_request_returns_body_on_success(config):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/clip/v2/resource/light"
        return httpx.Response(200, json={"data": []})

    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host="bridge.test", application_key="k", transport=httpx.MockTransport(handler))
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={
                "requestId": "r3",
                "action": "clipv2.request",
                "args": {"method": "GET", "path": "/clip/v2/resource/light"},
            },
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 200
        assert resp.body["result"]["status"] == 200
        assert resp.body["result"]["body"] == {"data": []}
    finally:
        await hue.close()
        await db.close()
