from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class V2ActionError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class V2ErrorEnvelope(BaseModel):
    requestId: str | None = None
    action: str | None = None
    ok: Literal[False] = False
    error: V2ActionError


class V2Warning(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class V2MatchOptions(BaseModel):
    mode: Literal["exact", "case_insensitive", "normalized", "fuzzy"] | None = None
    minConfidence: float | None = Field(default=None, ge=0.0, le=1.0)
    minGap: float | None = Field(default=None, ge=0.0, le=1.0)
    maxCandidates: int | None = Field(default=None, ge=1)


class V2XY(BaseModel):
    x: float
    y: float


class V2LightState(BaseModel):
    on: bool | None = None
    brightness: float | None = Field(default=None, ge=0.0, le=100.0)
    colorTempK: int | None = Field(default=None, gt=0)
    xy: V2XY | None = None


class V2VerifyOptions(BaseModel):
    mode: Literal["none", "poll", "sse", "poll_then_sse"] | None = None
    timeoutMs: int | None = Field(default=None, ge=0)
    pollIntervalMs: int | None = Field(default=None, ge=1)
    tolerances: dict[str, Any] | None = None


class _V2BaseRequest(BaseModel):
    requestId: str | None = None
    idempotencyKey: str | None = None


class V2BridgeSetHostArgs(BaseModel):
    bridgeHost: str = Field(..., min_length=1)


class V2BridgeSetHostRequest(_V2BaseRequest):
    action: Literal["bridge.set_host"]
    args: V2BridgeSetHostArgs


class V2BridgeSetHostResult(BaseModel):
    bridgeHost: str
    stored: bool


class V2BridgeSetHostSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["bridge.set_host"]
    ok: Literal[True] = True
    result: V2BridgeSetHostResult


class V2BridgePairArgs(BaseModel):
    devicetype: str | None = None


class V2BridgePairRequest(_V2BaseRequest):
    action: Literal["bridge.pair"]
    args: V2BridgePairArgs = Field(default_factory=V2BridgePairArgs)


class V2BridgePairResult(BaseModel):
    applicationKey: str
    stored: bool


class V2BridgePairSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["bridge.pair"]
    ok: Literal[True] = True
    result: V2BridgePairResult


class V2ClipV2RequestArgs(BaseModel):
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]
    path: str = Field(..., pattern=r"^/clip/v2/.*")
    body: dict[str, Any] | list[Any] | None = None


class V2ClipV2Request(_V2BaseRequest):
    action: Literal["clipv2.request"]
    args: V2ClipV2RequestArgs


class V2ClipV2Result(BaseModel):
    status: int
    body: Any


class V2ClipV2Success(BaseModel):
    requestId: str | None = None
    action: Literal["clipv2.request"]
    ok: Literal[True] = True
    result: V2ClipV2Result


class V2ResolveByNameArgs(BaseModel):
    rtype: str
    name: str
    match: V2MatchOptions | None = None


class V2ResolveByNameRequest(_V2BaseRequest):
    action: Literal["resolve.by_name"]
    args: V2ResolveByNameArgs


class V2ResolveByNameMatched(BaseModel):
    rid: str
    rtype: str
    name: str | None = None


class V2ResolveByNameResult(BaseModel):
    matched: V2ResolveByNameMatched
    confidence: float = Field(..., ge=0.0, le=1.0)


class V2ResolveByNameSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["resolve.by_name"]
    ok: Literal[True] = True
    result: V2ResolveByNameResult


class V2SetStateResult(BaseModel):
    requested: V2LightState
    applied: V2LightState
    observed: V2LightState | None = None
    verified: bool
    warnings: list[V2Warning] = Field(default_factory=list)


class V2LightSetArgs(BaseModel):
    rid: str | None = None
    name: str | None = None
    match: V2MatchOptions | None = None
    state: V2LightState
    verify: V2VerifyOptions | None = None


class V2LightSetRequest(_V2BaseRequest):
    action: Literal["light.set"]
    args: V2LightSetArgs


class V2LightSetSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["light.set"]
    ok: Literal[True] = True
    result: V2SetStateResult


class V2GroupedLightSetArgs(BaseModel):
    rid: str | None = None
    name: str | None = None
    match: V2MatchOptions | None = None
    state: V2LightState
    verify: V2VerifyOptions | None = None


class V2GroupedLightSetRequest(_V2BaseRequest):
    action: Literal["grouped_light.set"]
    args: V2GroupedLightSetArgs


class V2GroupedLightSetSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["grouped_light.set"]
    ok: Literal[True] = True
    result: V2SetStateResult


class V2SceneActivateArgs(BaseModel):
    rid: str | None = None
    name: str | None = None
    match: V2MatchOptions | None = None
    verify: V2VerifyOptions | None = None


class V2SceneActivateRequest(_V2BaseRequest):
    action: Literal["scene.activate"]
    args: V2SceneActivateArgs


class V2SceneActivateResult(BaseModel):
    status: int
    body: Any


class V2SceneActivateSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["scene.activate"]
    ok: Literal[True] = True
    result: V2SceneActivateResult


class V2RoomSetArgs(BaseModel):
    roomRid: str | None = None
    roomName: str | None = None
    match: V2MatchOptions | None = None
    state: V2LightState
    verify: V2VerifyOptions | None = None


class V2RoomSetRequest(_V2BaseRequest):
    action: Literal["room.set"]
    args: V2RoomSetArgs


class V2RoomSetResult(V2SetStateResult):
    roomRid: str
    groupedLightRid: str


class V2RoomSetSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["room.set"]
    ok: Literal[True] = True
    result: V2RoomSetResult


class V2ZoneSetImpact(BaseModel):
    roomCount: int | None = None
    groupedLightCount: int | None = None
    lightCount: int | None = None


class V2ZoneSetArgs(BaseModel):
    zoneRid: str | None = None
    zoneName: str | None = None
    match: V2MatchOptions | None = None
    state: V2LightState
    verify: V2VerifyOptions | None = None
    dryRun: bool | None = None


class V2ZoneSetRequest(_V2BaseRequest):
    action: Literal["zone.set"]
    args: V2ZoneSetArgs


class V2ZoneSetResult(V2SetStateResult):
    zoneRid: str
    groupedLightRid: str
    dryRun: bool | None = None
    impact: V2ZoneSetImpact | None = None


class V2ZoneSetSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["zone.set"]
    ok: Literal[True] = True
    result: V2ZoneSetResult


class V2InventorySnapshotArgs(BaseModel):
    ifRevision: int | None = Field(default=None, ge=0)


class V2InventorySnapshotRequest(_V2BaseRequest):
    action: Literal["inventory.snapshot"]
    args: V2InventorySnapshotArgs = Field(default_factory=V2InventorySnapshotArgs)


class V2InventorySnapshotNotModified(BaseModel):
    notModified: Literal[True] = True
    revision: int = Field(..., ge=0)


class V2InventoryRoom(BaseModel):
    rid: str
    name: str
    groupedLightRid: str


class V2InventoryZone(BaseModel):
    rid: str
    name: str
    groupedLightRid: str
    roomRids: list[str] | None = None


class V2InventoryLight(BaseModel):
    rid: str
    name: str
    ownerDeviceRid: str
    roomRid: str | None = None


class V2InventorySnapshotFull(BaseModel):
    notModified: Literal[False] = False
    bridgeId: str
    generatedAt: datetime
    revision: int = Field(..., ge=0)
    stale: bool
    staleReason: Literal[
        "not_configured",
        "bridge_unreachable",
        "sse_disconnected",
        "cache_too_old",
        "unknown",
    ] | None = None
    rooms: list[V2InventoryRoom]
    zones: list[V2InventoryZone]
    lights: list[V2InventoryLight]


V2InventorySnapshotResult = Annotated[
    Union[V2InventorySnapshotNotModified, V2InventorySnapshotFull],
    Field(discriminator="notModified"),
]


class V2InventorySnapshotSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["inventory.snapshot"]
    ok: Literal[True] = True
    result: V2InventorySnapshotResult


V2BatchStepRequest = Annotated[
    Union[
        V2BridgeSetHostRequest,
        V2BridgePairRequest,
        V2ClipV2Request,
        V2ResolveByNameRequest,
        V2LightSetRequest,
        V2GroupedLightSetRequest,
        V2SceneActivateRequest,
        V2RoomSetRequest,
        V2ZoneSetRequest,
        V2InventorySnapshotRequest,
    ],
    Field(discriminator="action"),
]


class V2ActionsBatchArgs(BaseModel):
    continueOnError: bool = False
    actions: list[V2BatchStepRequest] = Field(..., min_length=1)


class V2ActionsBatchRequest(_V2BaseRequest):
    action: Literal["actions.batch"]
    args: V2ActionsBatchArgs


class V2BatchStepResult(BaseModel):
    index: int = Field(..., ge=0)
    action: str
    requestId: str | None = None
    idempotencyKey: str | None = None
    ok: bool
    status: int
    result: Any | None = None
    error: V2ActionError | None = None


class V2ActionsBatchResult(BaseModel):
    continueOnError: bool
    steps: list[V2BatchStepResult]


class V2ActionsBatchSuccess(BaseModel):
    requestId: str | None = None
    action: Literal["actions.batch"]
    ok: Literal[True] = True
    result: V2ActionsBatchResult


V2ActionRequest = Annotated[
    Union[
        V2BridgeSetHostRequest,
        V2BridgePairRequest,
        V2ClipV2Request,
        V2ResolveByNameRequest,
        V2LightSetRequest,
        V2GroupedLightSetRequest,
        V2SceneActivateRequest,
        V2RoomSetRequest,
        V2ZoneSetRequest,
        V2InventorySnapshotRequest,
        V2ActionsBatchRequest,
    ],
    Field(discriminator="action"),
]


V2ActionSuccessResponse = Annotated[
    Union[
        V2BridgeSetHostSuccess,
        V2BridgePairSuccess,
        V2ClipV2Success,
        V2ResolveByNameSuccess,
        V2LightSetSuccess,
        V2GroupedLightSetSuccess,
        V2SceneActivateSuccess,
        V2RoomSetSuccess,
        V2ZoneSetSuccess,
        V2InventorySnapshotSuccess,
        V2ActionsBatchSuccess,
    ],
    Field(discriminator="action"),
]


class V2SseResourceRef(BaseModel):
    rid: str
    rtype: str


class V2SseEvent(BaseModel):
    ts: datetime
    type: str
    resource: V2SseResourceRef | None
    revision: int = Field(..., ge=0)
    eventId: int | None = Field(default=None, ge=0)
    data: Any | None = None

