from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import asyncio
import random


class HueTransportError(Exception):
    pass


class HueUpstreamError(Exception):
    def __init__(self, *, status_code: int, body: Any) -> None:
        super().__init__(f"Hue upstream error: {status_code}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class HueJSONishResult:
    status_code: int
    body: Any


class HueClient:
    def __init__(
        self,
        *,
        bridge_host: str | None,
        application_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._bridge_host = bridge_host
        self._application_key = application_key
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def configure(self, *, bridge_host: str | None, application_key: str | None) -> None:
        changed = (bridge_host != self._bridge_host) or (application_key != self._application_key)
        self._bridge_host = bridge_host
        self._application_key = application_key
        if changed and self._client:
            # Lazily recreated on next request.
            old = self._client
            self._client = None
            try:
                # Best-effort: closing is async, but we don't want to block here.
                import asyncio

                asyncio.create_task(old.aclose())
            except RuntimeError:
                pass

    @property
    def bridge_host(self) -> str | None:
        return self._bridge_host

    @property
    def application_key(self) -> str | None:
        return self._application_key

    def _base_url(self) -> str:
        if not self._bridge_host:
            raise HueTransportError("bridge_host not configured")
        return f"https://{self._bridge_host}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client:
            return self._client
        headers = {}
        if self._application_key:
            headers["hue-application-key"] = self._application_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url(),
            verify=False,
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers=headers,
            transport=self._transport,
        )
        return self._client

    async def request_jsonish(
        self,
        *,
        method: str,
        path: str,
        json_body: Any | None = None,
        retry: bool = False,
        max_attempts: int = 3,
        base_delay_ms: int = 200,
    ) -> HueJSONishResult:
        client = await self._get_client()
        attempts = max_attempts if retry else 1

        last_transport_error: Exception | None = None
        last_upstream_error: HueUpstreamError | None = None

        for attempt in range(1, attempts + 1):
            try:
                resp = await client.request(method, path, json=json_body)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
                last_transport_error = exc
                if attempt == attempts:
                    raise HueTransportError(str(exc)) from exc
                await self._sleep_backoff(attempt=attempt, base_delay_ms=base_delay_ms)
                continue

            body: Any
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
            else:
                body = resp.text

            if resp.status_code >= 400:
                err = HueUpstreamError(status_code=resp.status_code, body=body)
                last_upstream_error = err
                should_retry = retry and (resp.status_code == 429 or 500 <= resp.status_code <= 599)
                if should_retry and attempt < attempts:
                    await self._sleep_backoff(attempt=attempt, base_delay_ms=base_delay_ms)
                    continue
                raise err

            return HueJSONishResult(status_code=resp.status_code, body=body)

        if last_upstream_error:
            raise last_upstream_error
        if last_transport_error:
            raise HueTransportError(str(last_transport_error)) from last_transport_error
        raise HueTransportError("request failed")

    async def _sleep_backoff(self, *, attempt: int, base_delay_ms: int) -> None:
        # Exponential backoff with jitter.
        delay = (base_delay_ms / 1000.0) * (2 ** (attempt - 1))
        delay = delay * (0.5 + random.random())
        await asyncio.sleep(min(delay, 5.0))

    async def get_json(self, path: str) -> Any:
        result = await self.request_jsonish(method="GET", path=path)
        return result.body

    async def post_json(self, path: str, *, json_body: Any) -> Any:
        result = await self.request_jsonish(method="POST", path=path, json_body=json_body)
        return result.body

    async def stream_sse_json(self, path: str):
        client = await self._get_client()
        headers = {"Accept": "text/event-stream"}
        try:
            async with client.stream("GET", path, headers=headers, timeout=None) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise HueUpstreamError(status_code=resp.status_code, body=body.decode("utf-8", "ignore"))

                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line == "":
                        if data_lines:
                            payload = "\n".join(data_lines)
                            data_lines = []
                            try:
                                import json as _json

                                yield _json.loads(payload)
                            except Exception:
                                continue
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:") :].lstrip())
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
            raise HueTransportError(str(exc)) from exc
