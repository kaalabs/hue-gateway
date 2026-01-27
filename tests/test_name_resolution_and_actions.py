import json

import httpx
import pytest

from hue_gateway.actions import ActionDispatcher
from hue_gateway.config import AppConfig
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient
from hue_gateway.security import AuthContext


@pytest.mark.asyncio
async def test_resolve_by_name_returns_409_on_ambiguous_candidates():
    cfg = AppConfig(
        port=8000,
        bridge_host=None,
        application_key=None,
        auth_tokens=[],
        api_keys=[],
        cache_resync_seconds=300,
        fuzzy_match_threshold=0.50,
        fuzzy_match_autopick_threshold=0.95,
        fuzzy_match_margin=0.05,
        rate_limit_rps=5.0,
        rate_limit_burst=10,
        retry_max_attempts=3,
        retry_base_delay_ms=1,
    )

    db = Database(":memory:")
    await db.connect()
    # Two candidates with identical similarity to "lamp"
    await db.upsert_resource(rid="1", rtype="light", name="Lamp1", json_text=json.dumps({"id": "1"}))
    await db.upsert_resource(rid="2", rtype="light", name="Lamp2", json_text=json.dumps({"id": "2"}))
    await db.commit()
    await db.rebuild_name_index()

    hue = HueClient(bridge_host="bridge.test", application_key="k", transport=httpx.MockTransport(lambda r: None))  # type: ignore[arg-type]
    dispatcher = ActionDispatcher(db=db, hue=hue, config=cfg)
    try:
        resp = await dispatcher.dispatch(
            payload={"requestId": "r1", "action": "resolve.by_name", "args": {"rtype": "light", "name": "lamp"}},
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 409
        assert resp.body["error"]["code"] == "ambiguous_name"
        assert len(resp.body["error"]["details"]["candidates"]) == 2
    finally:
        await hue.close()
        await db.close()


@pytest.mark.asyncio
async def test_light_set_resolves_by_name_and_calls_bridge_put(config):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/clip/v2/resource/light/1"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["on"] == {"on": True}
        assert payload["dimming"]["brightness"] == 30.0
        return httpx.Response(200, json={"ok": True})

    db = Database(":memory:")
    await db.connect()
    await db.upsert_resource(
        rid="1",
        rtype="light",
        name="Kitchen Lamp",
        json_text=json.dumps({"id": "1", "metadata": {"name": "Kitchen Lamp"}}),
    )
    await db.commit()
    await db.rebuild_name_index()

    hue = HueClient(bridge_host="bridge.test", application_key="k", transport=httpx.MockTransport(handler))
    dispatcher = ActionDispatcher(db=db, hue=hue, config=config)
    try:
        resp = await dispatcher.dispatch(
            payload={
                "requestId": "r2",
                "action": "light.set",
                "args": {"name": "kitchen lamp", "on": True, "brightness": 30},
            },
            auth=AuthContext(credential="dev", scheme="bearer"),
        )
        assert resp.status_code == 200
        assert resp.body["ok"] is True
        assert resp.body["result"]["status"] == 200
        assert resp.body["result"]["body"] == {"ok": True}
    finally:
        await hue.close()
        await db.close()

