from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = Field(..., description="Process is alive.")


class ReadinessResponse(BaseModel):
    ready: bool = Field(..., description="True when the gateway can talk to the Hue Bridge.")
    reason: str | None = Field(
        default=None,
        description="When not ready, a short machine-readable reason (e.g. missing_bridge_host).",
    )
    details: Any | None = Field(
        default=None,
        description="Optional extra details for debugging; do not rely on this shape.",
    )


class _BaseActionRequest(BaseModel):
    requestId: str | None = Field(
        default=None,
        description="Optional client-provided id used for correlating logs and responses.",
        examples=["req-123", "chat-2026-01-27T12:00:00Z-0001"],
    )


class BridgeSetHostArgs(BaseModel):
    bridgeHost: str = Field(
        ...,
        description="Hue Bridge IP/hostname on the LAN (no scheme/path).",
        examples=["192.168.1.29"],
    )


class BridgeSetHostRequest(_BaseActionRequest):
    action: Literal["bridge.set_host"] = Field("bridge.set_host", description="Persist bridge host/IP in the gateway.")
    args: BridgeSetHostArgs


class BridgePairArgs(BaseModel):
    devicetype: str | None = Field(
        default="hue-gateway#docker",
        description="Bridge registration device type (free-form string).",
        examples=["hue-gateway#docker"],
    )


class BridgePairRequest(_BaseActionRequest):
    action: Literal["bridge.pair"] = Field("bridge.pair", description="Create/store a Hue application key (press button).")
    args: BridgePairArgs = Field(default_factory=BridgePairArgs)


class ClipV2RequestArgs(BaseModel):
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"] = Field(
        ..., description="HTTP method to use against the Hue Bridge."
    )
    path: str = Field(
        ...,
        description="Must start with `/clip/v2/` and must not contain scheme/host.",
        examples=["/clip/v2/resource/room"],
        pattern=r"^/clip/v2/.*",
    )
    body: dict[str, Any] | list[Any] | None = Field(
        default=None,
        description="Optional JSON body (object or array) for POST/PUT requests.",
    )


class ClipV2Request(_BaseActionRequest):
    action: Literal["clipv2.request"] = Field("clipv2.request", description="CLIP v2 pass-through request.")
    args: ClipV2RequestArgs


class ResolveByNameArgs(BaseModel):
    rtype: str = Field(..., description="Hue resource type (e.g., light, room, scene).", examples=["light"])
    name: str = Field(..., description="Human name to resolve (fuzzy matching).", examples=["Kitchen"])


class ResolveByNameRequest(_BaseActionRequest):
    action: Literal["resolve.by_name"] = Field("resolve.by_name", description="Resolve a resource rid by fuzzy name.")
    args: ResolveByNameArgs


class XYColor(BaseModel):
    x: float = Field(..., description="CIE xy x coordinate.", examples=[0.4])
    y: float = Field(..., description="CIE xy y coordinate.", examples=[0.3])


class LightSetArgs(BaseModel):
    rid: str | None = Field(default=None, description="Light resource id (preferred if known).")
    name: str | None = Field(default=None, description="Light name (fuzzy).")
    on: bool | None = Field(default=None, description="Turn on/off.")
    brightness: float | None = Field(default=None, ge=0.0, le=100.0, description="Brightness percent 0–100.")
    colorTempK: int | None = Field(default=None, gt=0, description="Color temperature in Kelvin.")
    xy: XYColor | None = Field(default=None, description="Color in CIE xy.")


class LightSetRequest(_BaseActionRequest):
    action: Literal["light.set"] = Field("light.set", description="Control a light by rid or fuzzy name.")
    args: LightSetArgs


class GroupedLightSetArgs(BaseModel):
    rid: str | None = Field(default=None, description="Grouped light resource id.")
    name: str | None = Field(default=None, description="Grouped light name (fuzzy).")
    on: bool | None = Field(default=None, description="Turn on/off.")
    brightness: float | None = Field(default=None, ge=0.0, le=100.0, description="Brightness percent 0–100.")
    colorTempK: int | None = Field(default=None, gt=0, description="Color temperature in Kelvin.")
    xy: XYColor | None = Field(default=None, description="Color in CIE xy.")


class GroupedLightSetRequest(_BaseActionRequest):
    action: Literal["grouped_light.set"] = Field("grouped_light.set", description="Control a grouped light by rid or name.")
    args: GroupedLightSetArgs


class SceneActivateArgs(BaseModel):
    rid: str | None = Field(default=None, description="Scene rid (preferred if known).")
    name: str | None = Field(default=None, description="Scene name (fuzzy).")


class SceneActivateRequest(_BaseActionRequest):
    action: Literal["scene.activate"] = Field("scene.activate", description="Activate a scene by rid or fuzzy name.")
    args: SceneActivateArgs


ActionRequest = Annotated[
    Union[
        BridgeSetHostRequest,
        BridgePairRequest,
        ClipV2Request,
        ResolveByNameRequest,
        LightSetRequest,
        GroupedLightSetRequest,
        SceneActivateRequest,
    ],
    Field(discriminator="action"),
]


class ActionError(BaseModel):
    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured extra error details for debugging/automation.",
    )


class _BaseActionSuccessResponse(BaseModel):
    requestId: str | None = Field(default=None, description="Echoed from the request (if provided).")
    ok: Literal[True] = Field(True, description="True for successful action execution.")


class BridgeSetHostResult(BaseModel):
    bridgeHost: str
    stored: bool


class BridgeSetHostSuccess(_BaseActionSuccessResponse):
    action: Literal["bridge.set_host"]
    result: BridgeSetHostResult


class BridgePairResult(BaseModel):
    applicationKey: str
    stored: bool


class BridgePairSuccess(_BaseActionSuccessResponse):
    action: Literal["bridge.pair"]
    result: BridgePairResult


class ClipV2Result(BaseModel):
    status: int = Field(..., description="HTTP status code returned by the Hue Bridge.")
    body: Any = Field(..., description="Hue Bridge response body (usually JSON).")


class ClipV2Success(_BaseActionSuccessResponse):
    action: Literal["clipv2.request"]
    result: ClipV2Result


class ResolveByNameMatched(BaseModel):
    rid: str
    rtype: str
    name: str | None = None


class ResolveByNameResult(BaseModel):
    matched: ResolveByNameMatched
    confidence: float


class ResolveByNameSuccess(_BaseActionSuccessResponse):
    action: Literal["resolve.by_name"]
    result: ResolveByNameResult


class LightSetSuccess(_BaseActionSuccessResponse):
    action: Literal["light.set"]
    result: ClipV2Result


class GroupedLightSetSuccess(_BaseActionSuccessResponse):
    action: Literal["grouped_light.set"]
    result: ClipV2Result


class SceneActivateSuccess(_BaseActionSuccessResponse):
    action: Literal["scene.activate"]
    result: ClipV2Result


ActionSuccessResponse = Annotated[
    Union[
        BridgeSetHostSuccess,
        BridgePairSuccess,
        ClipV2Success,
        ResolveByNameSuccess,
        LightSetSuccess,
        GroupedLightSetSuccess,
        SceneActivateSuccess,
    ],
    Field(discriminator="action"),
]


class ActionFailureResponse(BaseModel):
    requestId: str | None = Field(default=None, description="Echoed from the request (if provided).")
    action: str = Field(..., description="Echoed action name.")
    ok: Literal[False] = Field(False, description="False for failed action execution.")
    error: ActionError


ActionResponse = ActionSuccessResponse | ActionFailureResponse


class UnauthorizedResponse(BaseModel):
    detail: dict[str, Any] = Field(
        ...,
        description="FastAPI error envelope. For this API, typically `{ \"detail\": {\"error\":\"unauthorized\"} }`.",
        examples=[{"error": "unauthorized"}],
    )


class RateLimitedResponse(BaseModel):
    error: Literal["rate_limited"] = Field(
        "rate_limited",
        description="Gateway rate limit exceeded for the supplied credential.",
    )
