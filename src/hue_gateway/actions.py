from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

from hue_gateway.cache import normalize_name
from hue_gateway.config import AppConfig
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient, HueTransportError, HueUpstreamError
from hue_gateway.security import AuthContext


@dataclass(frozen=True)
class ActionHTTPResponse:
    status_code: int
    body: dict[str, Any]


class ActionError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


Handler = Callable[[str | None, dict[str, Any], AuthContext], Awaitable[dict[str, Any]]]


class ActionDispatcher:
    def __init__(self, *, db: Database, hue: HueClient, config: AppConfig) -> None:
        self.db = db
        self.hue = hue
        self.config = config
        self._handlers: dict[str, Handler] = {
            "bridge.set_host": self._bridge_set_host,
            "bridge.pair": self._bridge_pair,
            "clipv2.request": self._clipv2_request,
            "resolve.by_name": self._resolve_by_name,
            "light.set": self._light_set,
            "grouped_light.set": self._grouped_light_set,
            "scene.activate": self._scene_activate,
        }

    async def dispatch(self, *, payload: dict[str, Any], auth: AuthContext) -> ActionHTTPResponse:
        request_id = payload.get("requestId")
        action = payload.get("action")
        args = payload.get("args") or {}

        if not isinstance(action, str) or not action:
            return self._error_response(
                request_id=request_id,
                action="",
                err=ActionError(
                    status_code=400,
                    code="invalid_action",
                    message="Field 'action' must be a non-empty string",
                ),
            )
        if not isinstance(args, dict):
            return self._error_response(
                request_id=request_id,
                action=action,
                err=ActionError(
                    status_code=400,
                    code="invalid_args",
                    message="Field 'args' must be an object",
                ),
            )

        handler = self._handlers.get(action)
        if not handler:
            return self._error_response(
                request_id=request_id,
                action=action,
                err=ActionError(
                    status_code=400,
                    code="unknown_action",
                    message=f"Unknown action: {action}",
                ),
            )

        try:
            result = await handler(request_id, args, auth)
            return ActionHTTPResponse(
                status_code=200,
                body={"requestId": request_id, "action": action, "ok": True, "result": result},
            )
        except ActionError as err:
            return self._error_response(request_id=request_id, action=action, err=err)
        except HueTransportError as err:
            return self._error_response(
                request_id=request_id,
                action=action,
                err=ActionError(
                    status_code=424,
                    code="bridge_unreachable",
                    message="Hue Bridge unreachable",
                    details={"error": str(err)},
                ),
            )
        except HueUpstreamError as err:
            status_code = 429 if err.status_code == 429 else 502
            code = "bridge_rate_limited" if err.status_code == 429 else "bridge_error"
            return self._error_response(
                request_id=request_id,
                action=action,
                err=ActionError(
                    status_code=status_code,
                    code=code,
                    message="Hue Bridge returned an error",
                    details={"status": err.status_code, "body": err.body},
                ),
            )
        except Exception as err:
            return self._error_response(
                request_id=request_id,
                action=action,
                err=ActionError(status_code=500, code="internal_error", message=str(err)),
            )

    @staticmethod
    def _error_response(*, request_id: str | None, action: str, err: ActionError) -> ActionHTTPResponse:
        body: dict[str, Any] = {
            "requestId": request_id,
            "action": action,
            "ok": False,
            "error": {"code": err.code, "message": err.message, "details": err.details},
        }
        return ActionHTTPResponse(status_code=err.status_code, body=body)

    async def _bridge_pair(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        devicetype = args.get("devicetype") or "hue-gateway#docker"
        if not isinstance(devicetype, str):
            raise ActionError(status_code=400, code="invalid_devicetype", message="devicetype must be a string")

        response = await self.hue.post_json("/api", json_body={"devicetype": devicetype})
        # Expected Hue v1-style response: list of {"success": {"username": ...}} or {"error": {...}}
        if isinstance(response, list) and response:
            first = response[0]
            if isinstance(first, dict) and "error" in first:
                err = first["error"]
                if isinstance(err, dict) and int(err.get("type", 0)) == 101:
                    raise ActionError(
                        status_code=409,
                        code="link_button_not_pressed",
                        message="Press the Hue Bridge button and retry",
                    )
                raise ActionError(
                    status_code=502,
                    code="bridge_pairing_failed",
                    message="Bridge rejected pairing request",
                    details={"error": err},
                )
            if isinstance(first, dict) and "success" in first:
                success = first["success"]
                if isinstance(success, dict) and isinstance(success.get("username"), str):
                    application_key = success["username"]
                    await self.db.set_setting("application_key", application_key)
                    self.hue.configure(bridge_host=self.hue.bridge_host, application_key=application_key)
                    return {"applicationKey": application_key, "stored": True}

        raise ActionError(
            status_code=502,
            code="bridge_pairing_failed",
            message="Unexpected pairing response from bridge",
            details={"body": response},
        )

    async def _bridge_set_host(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        host = args.get("bridgeHost")
        if not isinstance(host, str) or not host.strip():
            raise ActionError(status_code=400, code="invalid_bridgeHost", message="bridgeHost must be a string")
        host = host.strip()
        if "://" in host or "/" in host or " " in host:
            raise ActionError(
                status_code=400,
                code="invalid_bridgeHost",
                message="bridgeHost must be an IP/hostname only (no scheme/path)",
            )

        await self.db.set_setting("bridge_host", host)
        self.hue.configure(bridge_host=host, application_key=self.hue.application_key)
        return {"bridgeHost": host, "stored": True}

    async def _clipv2_request(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        method = args.get("method")
        path = args.get("path")
        body = args.get("body")

        if method not in {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"}:
            raise ActionError(status_code=400, code="invalid_method", message="Invalid method")
        if not isinstance(path, str) or not path.startswith("/clip/v2/"):
            raise ActionError(
                status_code=400, code="invalid_path", message="path must start with /clip/v2/"
            )
        if path.startswith("//") or "://" in path or ".." in path:
            raise ActionError(status_code=400, code="invalid_path", message="Host override not allowed")

        json_body = None
        if body is not None:
            if not isinstance(body, (dict, list)):
                raise ActionError(status_code=400, code="invalid_body", message="body must be JSON object/array")
            json_body = body

        retry = method in {"GET", "HEAD", "OPTIONS"}
        result = await self.hue.request_jsonish(
            method=method,
            path=path,
            json_body=json_body,
            retry=retry,
            max_attempts=self.config.retry_max_attempts,
            base_delay_ms=self.config.retry_base_delay_ms,
        )
        return {"status": result.status_code, "body": result.body}

    async def _resolve_by_name(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        rtype = args.get("rtype")
        name = args.get("name")
        if not isinstance(rtype, str) or not rtype:
            raise ActionError(status_code=400, code="invalid_rtype", message="rtype must be a string")
        if not isinstance(name, str) or not name.strip():
            raise ActionError(status_code=400, code="invalid_name", message="name must be a string")

        matched = await self._resolve_name(rtype=rtype, name=name)
        return {
            "matched": {"rid": matched.rid, "rtype": rtype, "name": matched.name},
            "confidence": matched.confidence,
        }

    @dataclass(frozen=True)
    class _ResolvedName:
        rid: str
        name: str | None
        confidence: float

    async def _resolve_name(self, *, rtype: str, name: str) -> "_ResolvedName":
        query = normalize_name(name)
        candidates = await self.db.list_name_candidates(rtype=rtype)
        if not candidates:
            raise ActionError(status_code=404, code="not_found", message=f"No resources for rtype={rtype}")

        scored: list[tuple[float, str, str | None, str]] = []
        for cand_norm, rid, display_name in candidates:
            score = SequenceMatcher(None, query, cand_norm).ratio()
            scored.append((score, rid, display_name, cand_norm))
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_rid, best_name, _ = scored[0]
        if best_score >= self.config.fuzzy_match_autopick_threshold:
            return self._ResolvedName(rid=best_rid, name=best_name, confidence=best_score)

        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= self.config.fuzzy_match_threshold and (best_score - second_score) >= self.config.fuzzy_match_margin:
            return self._ResolvedName(rid=best_rid, name=best_name, confidence=best_score)

        top = scored[:5]
        raise ActionError(
            status_code=409,
            code="ambiguous_name",
            message=f"Multiple matches for {rtype} name",
            details={
                "candidates": [
                    {"rid": rid, "name": name, "confidence": score}
                    for score, rid, name, _ in top
                ]
            },
        )

    async def _light_set(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        rid = args.get("rid")
        name = args.get("name")
        if rid is None and name is None:
            raise ActionError(status_code=400, code="invalid_target", message="Provide rid or name")
        if rid is None:
            resolved = await self._resolve_name(rtype="light", name=str(name))
            rid = resolved.rid
        if not isinstance(rid, str) or not rid:
            raise ActionError(status_code=400, code="invalid_rid", message="rid must be a string")
        payload = self._build_light_payload(args)
        result = await self.hue.request_jsonish(method="PUT", path=f"/clip/v2/resource/light/{rid}", json_body=payload)
        return {"status": result.status_code, "body": result.body}

    async def _grouped_light_set(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        rid = args.get("rid")
        name = args.get("name")
        if rid is None and name is None:
            raise ActionError(status_code=400, code="invalid_target", message="Provide rid or name")
        if rid is None:
            resolved = await self._resolve_name(rtype="grouped_light", name=str(name))
            rid = resolved.rid
        if not isinstance(rid, str) or not rid:
            raise ActionError(status_code=400, code="invalid_rid", message="rid must be a string")
        payload = self._build_light_payload(args)
        result = await self.hue.request_jsonish(
            method="PUT", path=f"/clip/v2/resource/grouped_light/{rid}", json_body=payload
        )
        return {"status": result.status_code, "body": result.body}

    def _build_light_payload(self, args: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}

        if "on" in args:
            on_val = args.get("on")
            if not isinstance(on_val, bool):
                raise ActionError(status_code=400, code="invalid_on", message="on must be boolean")
            payload["on"] = {"on": on_val}

        if "brightness" in args and args.get("brightness") is not None:
            b = args.get("brightness")
            if not isinstance(b, (int, float)):
                raise ActionError(status_code=400, code="invalid_brightness", message="brightness must be number")
            brightness = max(0.1, min(100.0, float(b)))
            payload["dimming"] = {"brightness": brightness}

        if "colorTempK" in args and args.get("colorTempK") is not None:
            k = args.get("colorTempK")
            if not isinstance(k, (int, float)) or k <= 0:
                raise ActionError(status_code=400, code="invalid_colorTempK", message="colorTempK must be positive")
            mirek = int(round(1_000_000 / float(k)))
            payload["color_temperature"] = {"mirek": mirek}

        if "xy" in args and args.get("xy") is not None:
            xy = args.get("xy")
            if not isinstance(xy, dict) or "x" not in xy or "y" not in xy:
                raise ActionError(status_code=400, code="invalid_xy", message="xy must be {x,y}")
            x = xy.get("x")
            y = xy.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                raise ActionError(status_code=400, code="invalid_xy", message="xy.x and xy.y must be numbers")
            payload["color"] = {"xy": {"x": float(x), "y": float(y)}}

        if not payload:
            raise ActionError(status_code=400, code="empty_state", message="No state fields provided")
        return payload

    async def _scene_activate(self, request_id: str | None, args: dict[str, Any], _: AuthContext):
        rid = args.get("rid")
        name = args.get("name")
        if rid is None and name is None:
            raise ActionError(status_code=400, code="invalid_target", message="Provide rid or name")
        if rid is None:
            resolved = await self._resolve_name(rtype="scene", name=str(name))
            rid = resolved.rid
        if not isinstance(rid, str) or not rid:
            raise ActionError(status_code=400, code="invalid_rid", message="rid must be a string")
        payload = {"recall": {"action": "active"}}
        result = await self.hue.request_jsonish(method="PUT", path=f"/clip/v2/resource/scene/{rid}", json_body=payload)
        return {"status": result.status_code, "body": result.body}
