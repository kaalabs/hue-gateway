import httpx
import pytest

from hue_gateway.hue_client import HueClient, HueTransportError, HueUpstreamError


@pytest.mark.asyncio
async def test_hue_client_returns_json_body_on_success():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("hue-application-key") == "abc"
        return httpx.Response(200, json={"ok": True})

    client = HueClient(
        bridge_host="bridge.test",
        application_key="abc",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.request_jsonish(method="GET", path="/clip/v2/resource/bridge")
        assert result.status_code == 200
        assert result.body == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hue_client_raises_upstream_error_and_exposes_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"description": "nope"}]})

    client = HueClient(
        bridge_host="bridge.test",
        application_key="abc",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(HueUpstreamError) as exc:
            await client.request_jsonish(method="GET", path="/clip/v2/resource/light")
        assert exc.value.status_code == 404
        assert exc.value.body == {"errors": [{"description": "nope"}]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hue_client_retries_on_429_for_safe_methods():
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate_limited"})
        return httpx.Response(200, json={"ok": True})

    client = HueClient(
        bridge_host="bridge.test",
        application_key="abc",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.request_jsonish(
            method="GET", path="/clip/v2/resource/light", retry=True, max_attempts=3, base_delay_ms=1
        )
        assert result.body == {"ok": True}
        assert calls["n"] == 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hue_client_raises_transport_error_on_connect_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = HueClient(
        bridge_host="bridge.test",
        application_key="abc",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(HueTransportError):
            await client.request_jsonish(method="GET", path="/clip/v2/resource/bridge")
    finally:
        await client.close()
