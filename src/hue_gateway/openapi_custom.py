from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def install_custom_openapi(app: FastAPI) -> None:
    action_mapping = {
        "bridge.set_host": "#/components/schemas/BridgeSetHostRequest",
        "bridge.pair": "#/components/schemas/BridgePairRequest",
        "clipv2.request": "#/components/schemas/ClipV2Request",
        "resolve.by_name": "#/components/schemas/ResolveByNameRequest",
        "light.set": "#/components/schemas/LightSetRequest",
        "grouped_light.set": "#/components/schemas/GroupedLightSetRequest",
        "scene.activate": "#/components/schemas/SceneActivateRequest",
    }

    v2_action_mapping = {
        "bridge.set_host": "#/components/schemas/V2BridgeSetHostRequest",
        "bridge.pair": "#/components/schemas/V2BridgePairRequest",
        "clipv2.request": "#/components/schemas/V2ClipV2Request",
        "resolve.by_name": "#/components/schemas/V2ResolveByNameRequest",
        "light.set": "#/components/schemas/V2LightSetRequest",
        "grouped_light.set": "#/components/schemas/V2GroupedLightSetRequest",
        "scene.activate": "#/components/schemas/V2SceneActivateRequest",
        "room.set": "#/components/schemas/V2RoomSetRequest",
        "zone.set": "#/components/schemas/V2ZoneSetRequest",
        "inventory.snapshot": "#/components/schemas/V2InventorySnapshotRequest",
        "actions.batch": "#/components/schemas/V2ActionsBatchRequest",
    }

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        # Remove auto-added 422 responses for endpoints where we normalize validation errors into 400.
        try:
            actions_post = schema["paths"]["/v1/actions"]["post"]
            actions_post.get("responses", {}).pop("422", None)
        except Exception:
            pass

        try:
            actions_post = schema["paths"]["/v2/actions"]["post"]
            actions_post.get("responses", {}).pop("422", None)
        except Exception:
            pass

        # Ensure SSE endpoint advertises text/event-stream (not JSON).
        try:
            ev_get = schema["paths"]["/v1/events/stream"]["get"]
            content = ev_get.get("responses", {}).get("200", {}).get("content", {})
            if isinstance(content, dict):
                content.pop("application/json", None)
        except Exception:
            pass

        try:
            ev_get = schema["paths"]["/v2/events/stream"]["get"]
            content = ev_get.get("responses", {}).get("200", {}).get("content", {})
            if isinstance(content, dict):
                content.pop("application/json", None)
        except Exception:
            pass

        # Tighten /v1/actions request schema to `oneOf` + discriminator.
        try:
            actions_post = schema["paths"]["/v1/actions"]["post"]
            req_schema = (
                actions_post["requestBody"]["content"]["application/json"]["schema"]
            )
            any_of = req_schema.get("anyOf")
            if isinstance(any_of, list) and any_of:
                # Convert anyOf -> oneOf for better client generation, and add discriminator mapping.
                req_schema.pop("anyOf", None)
                req_schema["oneOf"] = any_of
                req_schema["discriminator"] = {"propertyName": "action", "mapping": action_mapping}
        except Exception:
            pass

        # Tighten /v2/actions request schema to `oneOf` + discriminator.
        try:
            actions_post = schema["paths"]["/v2/actions"]["post"]
            req_schema = actions_post["requestBody"]["content"]["application/json"]["schema"]
            any_of = req_schema.get("anyOf")
            if isinstance(any_of, list) and any_of:
                req_schema.pop("anyOf", None)
                req_schema["oneOf"] = any_of
                req_schema["discriminator"] = {"propertyName": "action", "mapping": v2_action_mapping}
        except Exception:
            pass

        # Tighten /v1/actions 200 response schema from anyOf -> oneOf if possible.
        try:
            actions_post = schema["paths"]["/v1/actions"]["post"]
            resp_schema = actions_post["responses"]["200"]["content"]["application/json"]["schema"]
            any_of = resp_schema.get("anyOf")
            if isinstance(any_of, list) and any_of:
                resp_schema.pop("anyOf", None)
                resp_schema["oneOf"] = any_of
        except Exception:
            pass

        # Tighten /v2/actions 2xx response schema from anyOf -> oneOf if possible.
        try:
            actions_post = schema["paths"]["/v2/actions"]["post"]
            for code in ("200", "207"):
                resp_schema = actions_post["responses"][code]["content"]["application/json"]["schema"]
                any_of = resp_schema.get("anyOf")
                if isinstance(any_of, list) and any_of:
                    resp_schema.pop("anyOf", None)
                    resp_schema["oneOf"] = any_of
        except Exception:
            pass

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[assignment]
