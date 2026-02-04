from __future__ import annotations

import time
from typing import Any

from hue_gateway.cache import CachedResource, ResourceCache
from hue_gateway.db import Database
from hue_gateway.event_hub import EventHub
from hue_gateway.v2.event_bus import V2EventBus
from hue_gateway.v2.schemas import V2LightState, V2XY


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_state_delta(resource: CachedResource) -> dict[str, Any] | None:
    if resource.rtype not in {"light", "grouped_light"}:
        return None
    data = resource.data
    if not isinstance(data, dict):
        return None
    state = V2LightState()
    on = data.get("on")
    if isinstance(on, dict) and isinstance(on.get("on"), bool):
        state.on = on["on"]
    dimming = data.get("dimming")
    if isinstance(dimming, dict) and isinstance(dimming.get("brightness"), (int, float)):
        state.brightness = float(dimming["brightness"])
    ct = data.get("color_temperature")
    if isinstance(ct, dict) and isinstance(ct.get("mirek"), (int, float)):
        mirek = float(ct["mirek"])
        if mirek > 0:
            state.colorTempK = int(round(1_000_000 / mirek))
    color = data.get("color")
    if isinstance(color, dict):
        xy = color.get("xy")
        if isinstance(xy, dict) and isinstance(xy.get("x"), (int, float)) and isinstance(xy.get("y"), (int, float)):
            state.xy = V2XY(x=float(xy["x"]), y=float(xy["y"]))
    return {"state": state.model_dump(mode="json")}


async def forward_v1_to_v2_loop(*, db: Database, cache: ResourceCache, hub: EventHub, bus: V2EventBus) -> None:
    subscription = await hub.subscribe()
    try:
        while True:
            event = await subscription.queue.get()
            revision = await db.get_setting_int("inventory_revision", default=0)

            resource = event.get("resource")
            v2_resource = None
            if isinstance(resource, dict) and isinstance(resource.get("rid"), str) and isinstance(resource.get("rtype"), str):
                v2_resource = {"rid": resource["rid"], "rtype": resource["rtype"]}

            data_delta: dict[str, Any] | None = None
            if v2_resource and isinstance(v2_resource.get("rid"), str):
                cached = cache.get(v2_resource["rid"])
                if cached:
                    data_delta = _parse_state_delta(cached)

            v2_event: dict[str, Any] = {
                "ts": event.get("ts") if isinstance(event.get("ts"), str) else _now_ts(),
                "type": event.get("type") if isinstance(event.get("type"), str) else "unknown",
                "resource": v2_resource,
                "revision": int(revision),
            }
            if data_delta is not None:
                v2_event["data"] = data_delta

            await bus.publish(v2_event)
    finally:
        await subscription.unsubscribe()

