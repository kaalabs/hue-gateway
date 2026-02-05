from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from hue_gateway.cache import normalize_name
from hue_gateway.config import AppConfig
from hue_gateway.db import Database
from hue_gateway.hue_client import HueClient, HueTransportError, HueUpstreamError
from hue_gateway.security import AuthContext
from hue_gateway.v2.idempotency import credential_fingerprint, mark_completed, mark_in_progress, request_hash
from hue_gateway.v2.schemas import V2ActionRequest, V2ErrorEnvelope, V2LightState, V2VerifyOptions, V2Warning, V2XY


@dataclass(frozen=True)
class V2HTTPResponse:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str] | None = None


class V2ActionError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


class V2Dispatcher:
    def __init__(self, *, db: Database, hue: HueClient, cache: Any, config: AppConfig) -> None:
        self.db = db
        self.hue = hue
        self.cache = cache
        self.config = config

    async def dispatch(
        self,
        *,
        payload: V2ActionRequest,
        auth: AuthContext,
        request_id: str | None,
        idempotency_key: str | None,
    ) -> V2HTTPResponse:
        if not idempotency_key:
            return await self._dispatch_impl(payload=payload, auth=auth, request_id=request_id, idempotency_key=None)

        credential_fp = credential_fingerprint(auth)
        req_hash = request_hash(action=payload.action, args=payload.model_dump(mode="json").get("args"))
        rec, inserted = await mark_in_progress(
            db=self.db,
            credential_fp=credential_fp,
            key=idempotency_key,
            action=payload.action,
            req_hash=req_hash,
            ttl_seconds=15 * 60,
        )

        if not inserted:
            if rec.status == "in_progress":
                if rec.action != payload.action or rec.request_hash != req_hash:
                    return self._error(
                        request_id=request_id,
                        action=payload.action,
                        err=V2ActionError(
                            status_code=409,
                            code="idempotency_key_reuse_mismatch",
                            message="Idempotency key reused with a different request",
                            details={"idempotencyKey": idempotency_key},
                        ),
                    )
                retry_ms = 250
                return self._error(
                    request_id=request_id,
                    action=payload.action,
                    err=V2ActionError(
                        status_code=409,
                        code="idempotency_in_progress",
                        message="An identical request is still in progress",
                        details={"retryAfterMs": retry_ms},
                        headers={"Retry-After": "1"},
                    ),
                )

            if rec.status == "completed":
                if rec.action != payload.action or rec.request_hash != req_hash:
                    return self._error(
                        request_id=request_id,
                        action=payload.action,
                        err=V2ActionError(
                            status_code=409,
                            code="idempotency_key_reuse_mismatch",
                            message="Idempotency key reused with a different request",
                            details={"idempotencyKey": idempotency_key},
                        ),
                    )
                if rec.response_status_code is None or rec.response_json is None:
                    return self._error(
                        request_id=request_id,
                        action=payload.action,
                        err=V2ActionError(
                            status_code=500,
                            code="internal_error",
                            message="Idempotency record missing stored response",
                            details={"idempotencyKey": idempotency_key},
                        ),
                    )
                try:
                    obj = json.loads(rec.response_json)
                except Exception:
                    obj = None
                if not isinstance(obj, dict):
                    return self._error(
                        request_id=request_id,
                        action=payload.action,
                        err=V2ActionError(
                            status_code=500,
                            code="internal_error",
                            message="Stored idempotency response is not a JSON object",
                            details={"idempotencyKey": idempotency_key},
                        ),
                    )
                obj["requestId"] = request_id
                return V2HTTPResponse(status_code=int(rec.response_status_code), body=obj)

        # We own the in-progress record.
        response = await self._dispatch_impl(payload=payload, auth=auth, request_id=request_id, idempotency_key=idempotency_key)
        try:
            await mark_completed(
                db=self.db,
                credential_fp=credential_fp,
                key=idempotency_key,
                action=payload.action,
                req_hash=req_hash,
                status_code=int(response.status_code),
                response_obj=response.body,
                ttl_seconds=15 * 60,
            )
        except Exception:
            pass
        return response

    async def _dispatch_impl(
        self,
        *,
        payload: V2ActionRequest,
        auth: AuthContext,
        request_id: str | None,
        idempotency_key: str | None,
    ) -> V2HTTPResponse:
        try:
            action = payload.action
            if action == "bridge.set_host":
                return await self._bridge_set_host(payload=payload, request_id=request_id)
            if action == "bridge.pair":
                return await self._bridge_pair(payload=payload, request_id=request_id)
            if action == "clipv2.request":
                return await self._clipv2_request(payload=payload, request_id=request_id)
            if action == "resolve.by_name":
                return await self._resolve_by_name(payload=payload, request_id=request_id)
            if action == "light.set":
                return await self._light_set(payload=payload, request_id=request_id)
            if action == "grouped_light.set":
                return await self._grouped_light_set(payload=payload, request_id=request_id)
            if action == "scene.activate":
                return await self._scene_activate(payload=payload, request_id=request_id)
            if action == "room.set":
                return await self._room_set(payload=payload, request_id=request_id)
            if action == "zone.set":
                return await self._zone_set(payload=payload, request_id=request_id)
            if action == "inventory.snapshot":
                return await self._inventory_snapshot(payload=payload, request_id=request_id)
            if action == "actions.batch":
                return await self._actions_batch(payload=payload, auth=auth, request_id=request_id, batch_key=idempotency_key)

            raise V2ActionError(status_code=400, code="unknown_action", message=f"Unknown action: {action}")
        except V2ActionError as err:
            return self._error(request_id=request_id, action=getattr(payload, "action", None), err=err)
        except HueTransportError as err:
            return self._error(
                request_id=request_id,
                action=getattr(payload, "action", None),
                err=V2ActionError(
                    status_code=424,
                    code="bridge_unreachable",
                    message="Hue Bridge unreachable",
                    details={"error": str(err)},
                ),
            )
        except HueUpstreamError as err:
            status_code = 429 if err.status_code == 429 else 502
            code = "bridge_rate_limited" if err.status_code == 429 else "bridge_error"
            return self._error(
                request_id=request_id,
                action=getattr(payload, "action", None),
                err=V2ActionError(
                    status_code=status_code,
                    code=code,
                    message="Hue Bridge returned an error",
                    details={"status": err.status_code, "body": err.body},
                ),
            )
        except Exception as err:
            return self._error(
                request_id=request_id,
                action=getattr(payload, "action", None),
                err=V2ActionError(status_code=500, code="internal_error", message="Internal error", details={"error": str(err)}),
            )

    def _error(self, *, request_id: str | None, action: str | None, err: V2ActionError) -> V2HTTPResponse:
        body: dict[str, Any] = V2ErrorEnvelope(
            requestId=request_id,
            action=action,
            ok=False,
            error={"code": err.code, "message": err.message, "details": err.details},
        ).model_dump(mode="json")
        return V2HTTPResponse(status_code=int(err.status_code), body=body, headers=err.headers)

    async def _bridge_set_host(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        host = payload.args.bridgeHost
        host = host.strip()
        if "://" in host or "/" in host or " " in host:
            raise V2ActionError(status_code=400, code="invalid_args", message="bridgeHost must be an IP/hostname only")
        await self.db.set_setting("bridge_host", host)
        self.hue.configure(bridge_host=host, application_key=self.hue.application_key)
        return V2HTTPResponse(
            status_code=200,
            body={"requestId": request_id, "action": "bridge.set_host", "ok": True, "result": {"bridgeHost": host, "stored": True}},
        )

    async def _bridge_pair(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        devicetype = payload.args.devicetype or "hue-gateway#docker"
        response = await self.hue.post_json("/api", json_body={"devicetype": devicetype})
        if isinstance(response, list) and response:
            first = response[0]
            if isinstance(first, dict) and "error" in first:
                err = first["error"]
                if isinstance(err, dict) and int(err.get("type", 0)) == 101:
                    raise V2ActionError(
                        status_code=409,
                        code="link_button_not_pressed",
                        message="Press the Hue Bridge button and retry",
                    )
                raise V2ActionError(
                    status_code=502,
                    code="bridge_error",
                    message="Bridge rejected pairing request",
                    details={"error": err},
                )
            if isinstance(first, dict) and "success" in first:
                success = first["success"]
                if isinstance(success, dict) and isinstance(success.get("username"), str):
                    application_key = success["username"]
                    await self.db.set_setting("application_key", application_key)
                    self.hue.configure(bridge_host=self.hue.bridge_host, application_key=application_key)
                    return V2HTTPResponse(
                        status_code=200,
                        body={
                            "requestId": request_id,
                            "action": "bridge.pair",
                            "ok": True,
                            "result": {"applicationKey": application_key, "stored": True},
                        },
                    )

        raise V2ActionError(status_code=502, code="bridge_error", message="Unexpected pairing response from bridge", details={"body": response})

    async def _clipv2_request(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        method = payload.args.method
        path = payload.args.path
        body = payload.args.body
        if path.startswith("//") or "://" in path or ".." in path:
            raise V2ActionError(status_code=400, code="invalid_args", message="Host override not allowed")
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
        return V2HTTPResponse(
            status_code=200,
            body={"requestId": request_id, "action": "clipv2.request", "ok": True, "result": {"status": result.status_code, "body": result.body}},
        )

    @dataclass(frozen=True)
    class _ResolvedName:
        rid: str
        name: str | None
        confidence: float

    async def _resolve_name(self, *, rtype: str, name: str) -> "_ResolvedName":
        query = normalize_name(name)
        candidates = await self.db.list_name_candidates(rtype=rtype)
        if not candidates:
            raise V2ActionError(status_code=404, code="not_found", message=f"No resources for rtype={rtype}")

        scored: list[tuple[float, str, str | None]] = []
        for cand_norm, rid, display_name in candidates:
            score = SequenceMatcher(None, query, cand_norm).ratio()
            scored.append((score, rid, display_name))
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_rid, best_name = scored[0]
        if best_score >= self.config.fuzzy_match_autopick_threshold:
            return self._ResolvedName(rid=best_rid, name=best_name, confidence=best_score)

        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= self.config.fuzzy_match_threshold and (best_score - second_score) >= self.config.fuzzy_match_margin:
            return self._ResolvedName(rid=best_rid, name=best_name, confidence=best_score)

        top = scored[:5]
        raise V2ActionError(
            status_code=409,
            code="ambiguous_name",
            message=f"Multiple matches for {rtype} name",
            details={"candidates": [{"rid": rid, "name": nm, "confidence": score} for score, rid, nm in top]},
        )

    async def _resolve_by_name(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        matched = await self._resolve_name(rtype=payload.args.rtype, name=payload.args.name)
        return V2HTTPResponse(
            status_code=200,
            body={
                "requestId": request_id,
                "action": "resolve.by_name",
                "ok": True,
                "result": {
                    "matched": {"rid": matched.rid, "rtype": payload.args.rtype, "name": matched.name},
                    "confidence": matched.confidence,
                },
            },
        )

    def _extract_grouped_light_rid(self, resource: dict[str, Any]) -> str | None:
        services = resource.get("services")
        if not isinstance(services, list):
            return None
        for svc in services:
            if not isinstance(svc, dict):
                continue
            if svc.get("rtype") == "grouped_light" and isinstance(svc.get("rid"), str):
                return svc["rid"]
            if svc.get("type") == "grouped_light" and isinstance(svc.get("id"), str):
                return svc["id"]
        return None

    def _parse_light_state(self, resource: dict[str, Any]) -> V2LightState:
        state = V2LightState()
        on = resource.get("on")
        if isinstance(on, dict) and isinstance(on.get("on"), bool):
            state.on = on["on"]
        dimming = resource.get("dimming")
        if isinstance(dimming, dict) and isinstance(dimming.get("brightness"), (int, float)):
            state.brightness = float(dimming["brightness"])
        ct = resource.get("color_temperature")
        if isinstance(ct, dict) and isinstance(ct.get("mirek"), (int, float)):
            mirek = float(ct["mirek"])
            if mirek > 0:
                state.colorTempK = int(round(1_000_000 / mirek))
        color = resource.get("color")
        if isinstance(color, dict):
            xy = color.get("xy")
            if isinstance(xy, dict) and isinstance(xy.get("x"), (int, float)) and isinstance(xy.get("y"), (int, float)):
                state.xy = V2XY(x=float(xy["x"]), y=float(xy["y"]))
        return state

    def _build_applied_payload(
        self, *, requested: V2LightState, resource: dict[str, Any] | None
    ) -> tuple[V2LightState, list[V2Warning], dict[str, Any]]:
        applied = V2LightState()
        warnings: list[V2Warning] = []
        payload: dict[str, Any] = {}

        if requested.on is not None:
            applied.on = bool(requested.on)
            payload["on"] = {"on": applied.on}

        if requested.brightness is not None:
            b = float(requested.brightness)
            clamped = max(0.0, min(100.0, b))
            if clamped == 0.0:
                clamped = 0.1
            if clamped != b:
                warnings.append(
                    V2Warning(code="clamped", message="brightness was clamped", details={"requested": b, "applied": clamped})
                )
            applied.brightness = clamped
            payload["dimming"] = {"brightness": clamped}

        if requested.colorTempK is not None:
            k = float(requested.colorTempK)
            if k <= 0:
                raise V2ActionError(status_code=400, code="invalid_args", message="colorTempK must be positive")
            if resource is not None and "color_temperature" not in resource:
                warnings.append(V2Warning(code="unsupported", message="colorTempK not supported by target", details={}))
            else:
                mirek = int(round(1_000_000 / k))
                # Clamp mirek range when available.
                if resource is not None:
                    ct = resource.get("color_temperature")
                    if isinstance(ct, dict):
                        vr = ct.get("mirek_valid_range")
                        if isinstance(vr, dict) and isinstance(vr.get("minimum"), (int, float)) and isinstance(vr.get("maximum"), (int, float)):
                            mn = int(vr["minimum"])
                            mx = int(vr["maximum"])
                            cm = max(mn, min(mx, mirek))
                            if cm != mirek:
                                warnings.append(
                                    V2Warning(
                                        code="clamped",
                                        message="colorTempK was clamped",
                                        details={"requestedMirek": mirek, "appliedMirek": cm},
                                    )
                                )
                            mirek = cm
                payload["color_temperature"] = {"mirek": mirek}
                applied.colorTempK = int(round(1_000_000 / float(mirek))) if mirek > 0 else None

        if requested.xy is not None:
            if resource is not None and "color" not in resource:
                warnings.append(V2Warning(code="unsupported", message="xy not supported by target", details={}))
            else:
                x = float(requested.xy.x)
                y = float(requested.xy.y)
                payload["color"] = {"xy": {"x": x, "y": y}}
                applied.xy = requested.xy

        if not payload:
            raise V2ActionError(status_code=400, code="invalid_args", message="No state fields provided")

        return applied, warnings, payload

    def _tolerances_for(self, *, rtype: str) -> dict[str, float]:
        grouped_like = rtype in {"grouped_light", "room", "zone"}
        return {
            "brightness": 25.0 if grouped_like else 5.0,
            "colorTempK": 800.0 if grouped_like else 200.0,
            "xyDistance": 0.15,
        }

    def _compare_state(
        self,
        *,
        applied: V2LightState,
        observed: V2LightState,
        rtype: str,
        verify_xy: bool,
    ) -> tuple[bool, dict[str, Any]]:
        tol = self._tolerances_for(rtype=rtype)
        mismatches: dict[str, Any] = {}

        if applied.on is not None:
            if observed.on is None or bool(observed.on) != bool(applied.on):
                mismatches["on"] = {"applied": applied.on, "observed": observed.on}

        if applied.brightness is not None:
            if observed.brightness is None or abs(float(observed.brightness) - float(applied.brightness)) > tol["brightness"]:
                mismatches["brightness"] = {"applied": applied.brightness, "observed": observed.brightness, "tolerance": tol["brightness"]}

        if applied.colorTempK is not None:
            if observed.colorTempK is None or abs(float(observed.colorTempK) - float(applied.colorTempK)) > tol["colorTempK"]:
                mismatches["colorTempK"] = {"applied": applied.colorTempK, "observed": observed.colorTempK, "tolerance": tol["colorTempK"]}

        if verify_xy and applied.xy is not None:
            if observed.xy is None:
                mismatches["xy"] = {"applied": applied.xy, "observed": None}
            else:
                dx = float(observed.xy.x) - float(applied.xy.x)
                dy = float(observed.xy.y) - float(applied.xy.y)
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > tol["xyDistance"]:
                    mismatches["xy"] = {"applied": applied.xy, "observed": observed.xy, "tolerance": tol["xyDistance"], "distance": dist}

        return (len(mismatches) == 0), mismatches

    async def _verify_poll(
        self,
        *,
        resource_path: str,
        applied: V2LightState,
        rtype: str,
        timeout_ms: int,
        poll_interval_ms: int,
        verify_xy: bool,
    ) -> tuple[bool, V2LightState | None, list[V2Warning]]:
        deadline = time.monotonic() + (max(0, int(timeout_ms)) / 1000.0)
        interval = max(10, int(poll_interval_ms)) / 1000.0
        last_observed: V2LightState | None = None
        warnings: list[V2Warning] = []

        while time.monotonic() <= deadline:
            raw = await self.hue.get_json(resource_path)
            data = raw.get("data") if isinstance(raw, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                observed = self._parse_light_state(data[0])
                last_observed = observed
                ok, mismatches = self._compare_state(applied=applied, observed=observed, rtype=rtype, verify_xy=verify_xy)
                if ok:
                    return True, observed, warnings
                warnings = [V2Warning(code="verify_mismatch", message="Observed state did not match yet", details=mismatches)]
            await asyncio.sleep(interval)

        return False, last_observed, warnings

    async def _set_state(
        self,
        *,
        target_rtype: str,
        rid: str,
        requested: V2LightState,
        verify: Any | None,
        implicit_verify: bool,
    ) -> dict[str, Any]:
        cached = self.cache.get(rid) if self.cache else None
        resource = cached.data if cached and isinstance(cached.data, dict) else None
        applied, warnings, hue_payload = self._build_applied_payload(requested=requested, resource=resource)

        await self.hue.request_jsonish(
            method="PUT",
            path=f"/clip/v2/resource/{target_rtype}/{rid}",
            json_body=hue_payload,
            retry=False,
        )

        verify_mode = None
        timeout_ms = 0
        poll_interval_ms = 0
        verify_xy = False

        if verify is None or getattr(verify, "mode", None) in (None, "none"):
            verified = False
            return {
                "requested": requested.model_dump(mode="json"),
                "applied": applied.model_dump(mode="json"),
                "observed": None,
                "verified": verified,
                "warnings": [w.model_dump(mode="json") for w in (warnings + [V2Warning(code="verify_skipped", message="Verification disabled", details={})])],
            }

        # If verification is enabled implicitly (room/zone defaults), do not verify xy unless the client opted in.
        verify_mode = getattr(verify, "mode", None) or "poll"
        timeout_ms = int(getattr(verify, "timeoutMs", None) or (2500 if implicit_verify else 2000))
        poll_interval_ms = int(getattr(verify, "pollIntervalMs", None) or 150)
        if applied.xy is not None:
            verify_xy = not implicit_verify
            if not verify_xy:
                warnings.append(V2Warning(code="xy_verify_skipped", message="xy verification skipped by default", details={}))

        # poll, sse, poll_then_sse all behave as poll initially (SSE verify is implemented later).
        verified, observed, verify_warnings = await self._verify_poll(
            resource_path=f"/clip/v2/resource/{target_rtype}/{rid}",
            applied=applied,
            rtype=target_rtype if target_rtype != "grouped_light" else "grouped_light",
            timeout_ms=timeout_ms,
            poll_interval_ms=poll_interval_ms,
            verify_xy=verify_xy,
        )
        all_warnings = warnings + verify_warnings

        return {
            "requested": requested.model_dump(mode="json"),
            "applied": applied.model_dump(mode="json"),
            "observed": observed.model_dump(mode="json") if observed is not None else None,
            "verified": bool(verified),
            "warnings": [w.model_dump(mode="json") for w in all_warnings],
        }

    async def _light_set(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        rid = payload.args.rid
        if rid is None and payload.args.name:
            rid = (await self._resolve_name(rtype="light", name=payload.args.name)).rid
        if not isinstance(rid, str) or not rid:
            raise V2ActionError(status_code=400, code="invalid_args", message="Provide rid or name")
        result = await self._set_state(
            target_rtype="light",
            rid=rid,
            requested=payload.args.state,
            verify=payload.args.verify,
            implicit_verify=False,
        )
        return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "light.set", "ok": True, "result": result})

    async def _grouped_light_set(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        rid = payload.args.rid
        if rid is None and payload.args.name:
            rid = (await self._resolve_name(rtype="grouped_light", name=payload.args.name)).rid
        if not isinstance(rid, str) or not rid:
            raise V2ActionError(status_code=400, code="invalid_args", message="Provide rid or name")
        result = await self._set_state(
            target_rtype="grouped_light",
            rid=rid,
            requested=payload.args.state,
            verify=payload.args.verify,
            implicit_verify=False,
        )
        return V2HTTPResponse(
            status_code=200,
            body={"requestId": request_id, "action": "grouped_light.set", "ok": True, "result": result},
        )

    async def _scene_activate(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        rid = payload.args.rid
        if rid is None and payload.args.name:
            rid = (await self._resolve_name(rtype="scene", name=payload.args.name)).rid
        if not isinstance(rid, str) or not rid:
            raise V2ActionError(status_code=400, code="invalid_args", message="Provide rid or name")
        hue_payload = {"recall": {"action": "active"}}
        result = await self.hue.request_jsonish(method="PUT", path=f"/clip/v2/resource/scene/{rid}", json_body=hue_payload)
        return V2HTTPResponse(
            status_code=200,
            body={"requestId": request_id, "action": "scene.activate", "ok": True, "result": {"status": result.status_code, "body": result.body}},
        )

    async def _room_set(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        room_rid = payload.args.roomRid
        if room_rid is None and payload.args.roomName:
            room_rid = (await self._resolve_name(rtype="room", name=payload.args.roomName)).rid
        if not isinstance(room_rid, str) or not room_rid:
            raise V2ActionError(status_code=400, code="invalid_args", message="Provide roomRid or roomName")

        room = await self.db.get_resource(room_rid)
        if not isinstance(room, dict):
            raise V2ActionError(status_code=404, code="not_found", message="Room not found", details={"roomRid": room_rid})
        grouped_rid = self._extract_grouped_light_rid(room)
        if not grouped_rid:
            raise V2ActionError(status_code=502, code="bridge_error", message="Room missing grouped_light service", details={"roomRid": room_rid})

        verify = payload.args.verify
        implicit_verify = verify is None  # default to verify enabled for room.set
        if verify is None:
            verify = V2VerifyOptions(mode="poll", timeoutMs=2500, pollIntervalMs=150)

        result = await self._set_state(
            target_rtype="grouped_light",
            rid=grouped_rid,
            requested=payload.args.state,
            verify=verify,
            implicit_verify=implicit_verify,
        )
        result["roomRid"] = room_rid
        result["groupedLightRid"] = grouped_rid
        return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "room.set", "ok": True, "result": result})

    async def _zone_set(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        zone_rid = payload.args.zoneRid
        if zone_rid is None and payload.args.zoneName:
            zone_rid = (await self._resolve_name(rtype="zone", name=payload.args.zoneName)).rid
        if not isinstance(zone_rid, str) or not zone_rid:
            raise V2ActionError(status_code=400, code="invalid_args", message="Provide zoneRid or zoneName")

        zone = await self.db.get_resource(zone_rid)
        if not isinstance(zone, dict):
            raise V2ActionError(status_code=404, code="not_found", message="Zone not found", details={"zoneRid": zone_rid})
        grouped_rid = self._extract_grouped_light_rid(zone)
        if not grouped_rid:
            raise V2ActionError(status_code=502, code="bridge_error", message="Zone missing grouped_light service", details={"zoneRid": zone_rid})

        if payload.args.dryRun:
            impact = {"roomCount": None, "groupedLightCount": 1, "lightCount": None}
            children = zone.get("children")
            if isinstance(children, list):
                impact["roomCount"] = sum(1 for c in children if isinstance(c, dict) and c.get("rtype") == "room")
            result = {
                "zoneRid": zone_rid,
                "groupedLightRid": grouped_rid,
                "dryRun": True,
                "impact": impact,
                "requested": payload.args.state.model_dump(mode="json"),
                "applied": payload.args.state.model_dump(mode="json"),
                "observed": None,
                "verified": False,
                "warnings": [V2Warning(code="dry_run", message="dryRun enabled; no changes applied", details={}).model_dump(mode="json")],
            }
            return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "zone.set", "ok": True, "result": result})

        verify = payload.args.verify
        implicit_verify = verify is None  # default to verify enabled for zone.set
        if verify is None:
            verify = V2VerifyOptions(mode="poll", timeoutMs=2500, pollIntervalMs=150)

        result = await self._set_state(
            target_rtype="grouped_light",
            rid=grouped_rid,
            requested=payload.args.state,
            verify=verify,
            implicit_verify=implicit_verify,
        )
        result["zoneRid"] = zone_rid
        result["groupedLightRid"] = grouped_rid
        result["dryRun"] = False
        return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "zone.set", "ok": True, "result": result})

    async def _inventory_snapshot(self, *, payload: Any, request_id: str | None) -> V2HTTPResponse:
        if not self.hue.bridge_host or not self.hue.application_key:
            revision = await self.db.get_setting_int("inventory_revision", default=0)
            result = {
                "notModified": False,
                "bridgeId": "unknown",
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "revision": revision,
                "stale": True,
                "staleReason": "not_configured",
                "rooms": [],
                "zones": [],
                "lights": [],
            }
            return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "inventory.snapshot", "ok": True, "result": result})

        revision = await self.db.get_setting_int("inventory_revision", default=0)
        if_rev = payload.args.ifRevision
        if if_rev is not None and int(if_rev) == int(revision):
            result = {"notModified": True, "revision": int(revision)}
            return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "inventory.snapshot", "ok": True, "result": result})

        rooms_raw = await self.db.list_resources(rtype="room")
        zones_raw = await self.db.list_resources(rtype="zone")
        lights_raw = await self.db.list_resources(rtype="light")

        def name_of(obj: dict[str, Any]) -> str:
            md = obj.get("metadata")
            if isinstance(md, dict) and isinstance(md.get("name"), str):
                return md["name"]
            if isinstance(obj.get("name"), str):
                return obj["name"]
            return ""

        rooms: list[dict[str, Any]] = []
        device_to_room: dict[str, str] = {}
        for r in rooms_raw:
            rid = r.get("id")
            if not isinstance(rid, str):
                continue
            grouped = self._extract_grouped_light_rid(r)
            if not grouped:
                continue
            rooms.append({"rid": rid, "name": name_of(r), "groupedLightRid": grouped})

            # Best-effort: map room -> devices so we can derive light.roomRid.
            children = r.get("children")
            if isinstance(children, list):
                for c in children:
                    if not isinstance(c, dict):
                        continue
                    child_rid = c.get("rid") or c.get("id")
                    child_rtype = c.get("rtype") or c.get("type")
                    if child_rtype == "device" and isinstance(child_rid, str) and child_rid:
                        device_to_room[child_rid] = rid

        light_to_room: dict[str, str] = {}
        lights: list[dict[str, Any]] = []
        for l in lights_raw:
            rid = l.get("id")
            if not isinstance(rid, str):
                continue
            owner = l.get("owner")
            owner_rid = ""
            if isinstance(owner, dict) and isinstance(owner.get("rid"), str):
                owner_rid = owner["rid"]
            room_rid = device_to_room.get(owner_rid)
            if room_rid:
                light_to_room[rid] = room_rid
            lights.append(
                {
                    "rid": rid,
                    "name": name_of(l),
                    "ownerDeviceRid": owner_rid,
                    "roomRid": room_rid,
                }
            )

        zones: list[dict[str, Any]] = []
        for z in zones_raw:
            rid = z.get("id")
            if not isinstance(rid, str):
                continue
            grouped = self._extract_grouped_light_rid(z)
            if not grouped:
                continue

            # Hue bridges may model zone children as rooms, lights, and/or devices.
            room_rids: set[str] = set()
            children = z.get("children")
            if isinstance(children, list):
                for c in children:
                    if not isinstance(c, dict):
                        continue
                    child_rid = c.get("rid") or c.get("id")
                    child_rtype = c.get("rtype") or c.get("type")
                    if not isinstance(child_rid, str) or not child_rid or not isinstance(child_rtype, str):
                        continue

                    if child_rtype == "room":
                        room_rids.add(child_rid)
                        continue
                    if child_rtype == "light":
                        mapped = light_to_room.get(child_rid)
                        if mapped:
                            room_rids.add(mapped)
                        continue
                    if child_rtype == "device":
                        mapped = device_to_room.get(child_rid)
                        if mapped:
                            room_rids.add(mapped)
                        continue

            zones.append(
                {
                    "rid": rid,
                    "name": name_of(z),
                    "groupedLightRid": grouped,
                    "roomRids": sorted(room_rids) or None,
                }
            )

        bridge_id = "unknown"
        try:
            bridge = await self.hue.get_json("/clip/v2/resource/bridge")
            data = bridge.get("data") if isinstance(bridge, dict) else None
            if isinstance(data, list) and data and isinstance(data[0], dict) and isinstance(data[0].get("id"), str):
                bridge_id = data[0]["id"]
        except Exception:
            pass

        result = {
            "notModified": False,
            "bridgeId": bridge_id,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "revision": int(revision),
            "stale": False,
            "staleReason": None,
            "rooms": rooms,
            "zones": zones,
            "lights": lights,
        }
        return V2HTTPResponse(status_code=200, body={"requestId": request_id, "action": "inventory.snapshot", "ok": True, "result": result})

    async def _actions_batch(
        self,
        *,
        payload: Any,
        auth: AuthContext,
        request_id: str | None,
        batch_key: str | None,
    ) -> V2HTTPResponse:
        continue_on_error = bool(payload.args.continueOnError)
        steps = payload.args.actions
        results: list[dict[str, Any]] = []
        batch_request_id = request_id or payload.requestId
        batch_key = batch_key or payload.idempotencyKey

        failed_index: int | None = None
        failed_error: dict[str, Any] | None = None
        failed_status: int | None = None

        for i, step in enumerate(steps):
            step_request_id = step.requestId or (f"{batch_request_id}:{i}" if batch_request_id else None)
            step_key = step.idempotencyKey or (f"{batch_key}:{i}" if batch_key else None)
            resp = await self.dispatch(payload=step, auth=auth, request_id=step_request_id, idempotency_key=step_key)

            body = resp.body
            step_result: dict[str, Any] = {
                "index": i,
                "action": getattr(step, "action", ""),
                "requestId": step_request_id,
                "idempotencyKey": step_key,
                "ok": bool(body.get("ok")),
                "status": int(resp.status_code),
            }
            if body.get("ok") is True:
                step_result["result"] = body.get("result")
            else:
                step_result["error"] = body.get("error")
            results.append(step_result)

            if resp.status_code >= 400 and failed_index is None:
                failed_index = i
                failed_status = int(resp.status_code)
                failed_error = body.get("error") if isinstance(body.get("error"), dict) else None

            if not continue_on_error and resp.status_code >= 400:
                break

        # continueOnError=true => always 207 with per-step results.
        if continue_on_error:
            return V2HTTPResponse(
                status_code=207,
                body={
                    "requestId": request_id,
                    "action": "actions.batch",
                    "ok": True,
                    "result": {"continueOnError": True, "steps": results},
                },
            )

        # stop-on-error => 200 if all ok, otherwise failing step status with canonical error envelope
        if failed_index is None or failed_status is None:
            return V2HTTPResponse(
                status_code=200,
                body={
                    "requestId": request_id,
                    "action": "actions.batch",
                    "ok": True,
                    "result": {"continueOnError": False, "steps": results},
                },
            )

        err_code = "internal_error"
        err_message = "Batch step failed"
        err_details: dict[str, Any] = {"failedStepIndex": failed_index, "steps": results}
        if isinstance(failed_error, dict):
            if isinstance(failed_error.get("code"), str):
                err_code = failed_error["code"]
            if isinstance(failed_error.get("message"), str):
                err_message = failed_error["message"]

        raise V2ActionError(
            status_code=int(failed_status),
            code=err_code,
            message="Batch step failed",
            details=err_details,
        )
