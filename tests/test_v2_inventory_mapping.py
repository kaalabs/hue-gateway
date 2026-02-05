import json

import httpx
import pytest

from hue_gateway.config import AppConfig
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient
from hue_gateway.security import AuthContext
from hue_gateway.v2.dispatcher import V2Dispatcher
from hue_gateway.v2.schemas import V2InventorySnapshotRequest


@pytest.mark.asyncio
async def test_inventory_snapshot_derives_light_roomRid_and_zone_roomRids():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/clip/v2/resource/bridge":
            return httpx.Response(200, json={"errors": [], "data": [{"id": "bridge-1"}]})
        return httpx.Response(404, json={"errors": [{"description": "not found"}]})

    cfg = AppConfig(
        port=8000,
        bridge_host="bridge.test",
        application_key="k",
        auth_tokens=["dev-token"],
        api_keys=[],
        cache_resync_seconds=300,
        fuzzy_match_threshold=0.90,
        fuzzy_match_autopick_threshold=0.95,
        fuzzy_match_margin=0.05,
        rate_limit_rps=1000.0,
        rate_limit_burst=1000,
        retry_max_attempts=1,
        retry_base_delay_ms=1,
    )

    db = Database(":memory:")
    await db.connect()
    hue = None
    try:
        room_rid = "room-1"
        device_rid = "dev-1"
        light_rid = "light-1"
        zone_rid = "zone-1"

        room_obj = {
            "id": room_rid,
            "type": "room",
            "metadata": {"name": "Room A"},
            "children": [{"rid": device_rid, "rtype": "device"}],
            "services": [{"rid": "gl-room", "rtype": "grouped_light"}],
        }
        light_obj = {
            "id": light_rid,
            "type": "light",
            "metadata": {"name": "Lamp"},
            "owner": {"rid": device_rid, "rtype": "device"},
        }
        zone_obj = {
            "id": zone_rid,
            "type": "zone",
            "metadata": {"name": "Zone Z"},
            "children": [{"rid": light_rid, "rtype": "light"}],
            "services": [{"rid": "gl-zone", "rtype": "grouped_light"}],
        }

        await db.upsert_resource(rid=room_rid, rtype="room", name="Room A", json_text=json.dumps(room_obj))
        await db.upsert_resource(rid=light_rid, rtype="light", name="Lamp", json_text=json.dumps(light_obj))
        await db.upsert_resource(rid=zone_rid, rtype="zone", name="Zone Z", json_text=json.dumps(zone_obj))
        await db.commit()

        hue = HueClient(
            bridge_host=cfg.bridge_host,
            application_key=cfg.application_key,
            transport=httpx.MockTransport(handler),
        )
        dispatcher = V2Dispatcher(db=db, hue=hue, cache=None, config=cfg)

        resp = await dispatcher.dispatch(
            payload=V2InventorySnapshotRequest(action="inventory.snapshot"),
            auth=AuthContext(credential="dev-token", scheme="bearer"),
            request_id="r1",
            idempotency_key=None,
        )
        assert resp.status_code == 200
        assert resp.body["ok"] is True
        result = resp.body["result"]

        light = next(l for l in result["lights"] if l["rid"] == light_rid)
        assert light["roomRid"] == room_rid

        zone = next(z for z in result["zones"] if z["rid"] == zone_rid)
        assert zone["roomRids"] == [room_rid]
    finally:
        if hue is not None:
            await hue.close()
        await db.close()
