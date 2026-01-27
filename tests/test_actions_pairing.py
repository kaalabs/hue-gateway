import httpx
import pytest

from hue_gateway.actions import ActionDispatcher
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient
from hue_gateway.security import AuthContext


@pytest.mark.asyncio
async def test_bridge_set_host_stores_setting(config):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host=None, application_key=None, transport=httpx.MockTransport(handler))
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={
                "requestId": "r0",
                "action": "bridge.set_host",
                "args": {"bridgeHost": "192.168.1.2"},
            },
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 200
        assert await db.get_setting("bridge_host") == "192.168.1.2"
        assert hue.bridge_host == "192.168.1.2"
    finally:
        await hue.close()
        await db.close()


@pytest.mark.asyncio
async def test_bridge_pair_requires_button_press(config):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api"
        return httpx.Response(200, json=[{"error": {"type": 101, "description": "link button not pressed"}}])

    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host="bridge.test", application_key=None, transport=httpx.MockTransport(handler))
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={"requestId": "r1", "action": "bridge.pair", "args": {"devicetype": "x"}},
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 409
        assert resp.body["ok"] is False
        assert resp.body["error"]["code"] == "link_button_not_pressed"
    finally:
        await hue.close()
        await db.close()


@pytest.mark.asyncio
async def test_bridge_pair_stores_application_key(config):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"success": {"username": "appkey123"}}])

    db = Database(":memory:")
    await db.connect()
    hue = HueClient(bridge_host="bridge.test", application_key=None, transport=httpx.MockTransport(handler))
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={"requestId": "r2", "action": "bridge.pair", "args": {}},
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 200
        assert resp.body["ok"] is True
        assert resp.body["result"]["applicationKey"] == "appkey123"
        assert await db.get_setting("application_key") == "appkey123"
    finally:
        await hue.close()
        await db.close()
