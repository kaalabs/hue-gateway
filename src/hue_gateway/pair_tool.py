from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx


def _pick_first_csv(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    for item in value.split(","):
        item = item.strip()
        if item:
            return item
    return None


def _headers(token: str | None, api_key: str | None) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    if api_key:
        return {"X-API-Key": api_key}
    return {}


def _post_action(client: httpx.Client, url: str, headers: dict[str, str], action: str, args: dict[str, Any]):
    resp = client.post(
        f"{url}/v1/actions",
        headers={**headers, "Content-Type": "application/json"},
        json={"action": action, "args": args},
        timeout=10.0,
    )
    return resp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hue-gateway-pair")
    parser.add_argument("--gateway-url", default=os.getenv("HUE_GATEWAY_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("HUE_GATEWAY_TOKEN") or _pick_first_csv("GATEWAY_AUTH_TOKENS"))
    parser.add_argument("--api-key", default=os.getenv("HUE_GATEWAY_API_KEY") or _pick_first_csv("GATEWAY_API_KEYS"))
    parser.add_argument("--bridge-host", default=os.getenv("HUE_BRIDGE_HOST"))
    parser.add_argument("--devicetype", default="hue-gateway#docker")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--interval-ms", type=int, default=1500)
    parser.add_argument("--print-key", action="store_true", help="Print the application key (sensitive).")
    parser.add_argument("--verify", action="store_true", help="Verify by calling CLIP v2 bridge resource.")
    args = parser.parse_args(argv)

    if not args.token and not args.api_key:
        print("Missing gateway credentials. Provide --token or --api-key (or set env vars).", file=sys.stderr)
        raise SystemExit(2)

    gateway_url = args.gateway_url.rstrip("/")
    headers = _headers(args.token, args.api_key)

    with httpx.Client() as client:
        # Basic connectivity check
        try:
            health = client.get(f"{gateway_url}/healthz", timeout=5.0)
        except Exception as exc:
            print(f"Failed to reach gateway at {gateway_url}: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if health.status_code != 200:
            print(f"Gateway health check failed: HTTP {health.status_code}", file=sys.stderr)
            raise SystemExit(1)

        if args.bridge_host:
            print(f"Setting bridge host to {args.bridge_host}...")
            resp = _post_action(
                client, gateway_url, headers, "bridge.set_host", {"bridgeHost": args.bridge_host}
            )
            if resp.status_code != 200:
                print(f"Failed to set bridge host: HTTP {resp.status_code} {resp.text}", file=sys.stderr)
                raise SystemExit(1)

        print("Pairing requires the physical Hue Bridge button.")
        print("Press the bridge button now. Pairing attempts will run until success or timeout.")

        deadline = time.time() + args.timeout_seconds
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            resp = _post_action(
                client,
                gateway_url,
                headers,
                "bridge.pair",
                {"devicetype": args.devicetype},
            )

            try:
                payload = resp.json()
            except Exception:
                payload = {"raw": resp.text}

            if resp.status_code == 200 and isinstance(payload, dict) and payload.get("ok") is True:
                result = payload.get("result") or {}
                key = result.get("applicationKey")
                print("Paired successfully. Application key stored in gateway SQLite.")
                if args.print_key and isinstance(key, str):
                    print(f"Application key: {key}")
                elif isinstance(key, str):
                    print(f"Application key (masked): {key[:6]}…{key[-4:]}")

                if args.verify:
                    v = _post_action(
                        client,
                        gateway_url,
                        headers,
                        "clipv2.request",
                        {"method": "GET", "path": "/clip/v2/resource/bridge"},
                    )
                    print(f"Verify: HTTP {v.status_code}")
                raise SystemExit(0)

            err = payload.get("error") if isinstance(payload, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            if resp.status_code == 409 and code == "link_button_not_pressed":
                remaining = int(deadline - time.time())
                print(f"[{attempt}] Button not pressed yet. Retrying… ({remaining}s left)")
                time.sleep(max(0.1, args.interval_ms / 1000.0))
                continue

            if resp.status_code == 429:
                time.sleep(max(0.5, args.interval_ms / 1000.0))
                continue

            print(f"Pairing failed: HTTP {resp.status_code} {payload}", file=sys.stderr)
            raise SystemExit(1)

    raise SystemExit(1)


if __name__ == "__main__":
    main()

