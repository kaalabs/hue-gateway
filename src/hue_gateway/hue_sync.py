from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from hue_gateway.cache import ResourceCache
from hue_gateway.db import Database
from hue_gateway.event_hub import EventHub
from hue_gateway.hue_client import HueClient, HueTransportError, HueUpstreamError


CORE_RESOURCE_TYPES = ["device", "light", "room", "zone", "grouped_light", "scene"]


def _extract_name(resource: dict[str, Any]) -> str | None:
    metadata = resource.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return metadata["name"]
    if isinstance(resource.get("name"), str):
        return resource["name"]
    return None


async def sync_core_resources(*, db: Database, hue: HueClient, cache: ResourceCache) -> None:
    for rtype in CORE_RESOURCE_TYPES:
        payload = await hue.get_json(f"/clip/v2/resource/{rtype}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            if not isinstance(rid, str) or not rid:
                continue
            name = _extract_name(item)
            await db.upsert_resource(
                rid=rid,
                rtype=rtype,
                name=name,
                json_text=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
                updated_at=int(time.time()),
            )
            cache.upsert(rid=rid, rtype=rtype, name=name, data=item)

    await db.commit()
    await db.rebuild_name_index()


async def resync_loop(*, db: Database, hue: HueClient, cache: ResourceCache, seconds: int) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await sync_core_resources(db=db, hue=hue, cache=cache)
        except Exception:
            # Best-effort: next interval will retry.
            continue


async def iter_sse_json(hue: HueClient):
    """
    Yields parsed JSON objects from the bridge SSE endpoint.
    """
    async for obj in hue.stream_sse_json("/eventstream/clip/v2"):
        yield obj


async def sse_ingest_loop(*, db: Database, hue: HueClient, cache: ResourceCache, hub: EventHub, resync_seconds: int):
    backoff = 1.0
    while True:
        try:
            async for msg in iter_sse_json(hue):
                await _process_bridge_event_message(
                    msg=msg, db=db, hue=hue, cache=cache, hub=hub
                )
            backoff = 1.0
        except (HueTransportError, HueUpstreamError):
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            try:
                await sync_core_resources(db=db, hue=hue, cache=cache)
            except Exception:
                pass
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue


async def _process_bridge_event_message(
    *,
    msg: Any,
    db: Database,
    hue: HueClient,
    cache: ResourceCache,
    hub: EventHub,
) -> None:
    events: list[Any]
    if isinstance(msg, list):
        events = msg
    else:
        events = [msg]

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, list):
            continue

        for ref in data:
            if not isinstance(ref, dict):
                continue
            rid = ref.get("id")
            rtype = ref.get("type")
            if not isinstance(rid, str) or not isinstance(rtype, str):
                continue

            if event_type in {"delete", "remove"}:
                await db.delete_resource(rid)
                await db.commit()
                cache.delete(rid=rid)
                await hub.publish(
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "source": "hue-bridge",
                        "type": "resource.deleted",
                        "resource": {"rid": rid, "rtype": rtype},
                        "data": {},
                    }
                )
                continue

            # Fetch full resource to avoid persisting partial SSE payloads.
            full = await hue.get_json(f"/clip/v2/resource/{rtype}/{rid}")
            resource_list = full.get("data") if isinstance(full, dict) else None
            if not isinstance(resource_list, list) or not resource_list:
                continue
            resource = resource_list[0]
            if not isinstance(resource, dict):
                continue
            name = _extract_name(resource)
            await db.upsert_resource(
                rid=rid,
                rtype=rtype,
                name=name,
                json_text=json.dumps(resource, separators=(",", ":"), ensure_ascii=False),
                updated_at=int(time.time()),
            )
            await db.commit()
            await db.delete_name_index_for_rid(rid)
            if name:
                name_norm = " ".join(name.strip().lower().split())
                if name_norm:
                    await db.insert_name_index(rtype=rtype, name_norm=name_norm, rid=rid)
                    await db.commit()

            cache.upsert(rid=rid, rtype=rtype, name=name, data=resource)
            await hub.publish(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "hue-bridge",
                    "type": "resource.updated",
                    "resource": {"rid": rid, "rtype": rtype},
                    "data": {},
                }
            )

