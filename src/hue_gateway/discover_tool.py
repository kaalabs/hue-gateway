from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx


SSDP_ADDR = ("239.255.255.250", 1900)


@dataclass(frozen=True)
class DiscoveredBridge:
    ip: str
    source: str  # ssdp | mdns | scan
    location: str | None = None
    udn: str | None = None
    model: str | None = None
    friendly_name: str | None = None
    raw: dict[str, Any] | None = None


def _parse_httpish_headers(packet: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in packet.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def _ip_from_location(location: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(location)
        if parsed.hostname:
            return parsed.hostname
    except Exception:
        return None
    return None


def _looks_like_hue_description(xml_text: str) -> bool:
    # Heuristic per common Hue UPnP description.xml patterns: Basic:1 + friendly name contains hue.
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False

    ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
    device = root.find(".//upnp:device", ns)
    if device is None:
        device = root.find(".//device")

    def _find_text(path: str) -> str | None:
        el = device.find(path, ns) if device is not None else None
        if el is None and device is not None:
            el = device.find(path.replace("upnp:", ""))
        if el is not None and el.text:
            return el.text.strip()
        return None

    device_type = _find_text("upnp:deviceType") or _find_text("deviceType")
    friendly = _find_text("upnp:friendlyName") or _find_text("friendlyName")
    manufacturer = _find_text("upnp:manufacturer") or _find_text("manufacturer")
    model_name = _find_text("upnp:modelName") or _find_text("modelName")

    if device_type and "urn:schemas-upnp-org:device:basic:1" in device_type.lower():
        # Accept Philips/Signify strings or a friendlyName/modelName containing hue.
        hay = " ".join([friendly or "", manufacturer or "", model_name or ""]).lower()
        return "hue" in hay or "philips" in hay or "signify" in hay

    return False


def _extract_upnp_fields(xml_text: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {"udn": None, "model": None, "friendly_name": None}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
    device = root.find(".//upnp:device", ns)
    if device is None:
        device = root.find(".//device")
    if device is None:
        return out

    def _text(tag: str) -> str | None:
        el = device.find(tag, ns)
        if el is None:
            el = device.find(tag.replace("upnp:", ""))
        if el is not None and el.text:
            return el.text.strip()
        return None

    out["udn"] = _text("upnp:UDN") or _text("UDN")
    out["model"] = _text("upnp:modelName") or _text("modelName")
    out["friendly_name"] = _text("upnp:friendlyName") or _text("friendlyName")
    return out


def ssdp_discover(*, timeout_seconds: float = 3.0, st: str = "ssdp:all") -> list[DiscoveredBridge]:
    # Best practice: simple M-SEARCH; avoid quoting MAN value (some bridges are picky).
    msg = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}",
            "MAN: ssdp:discover",
            "MX: 3",
            f"ST: {st}",
            "",
            "",
        ]
    ).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.2)
        sock.sendto(msg, SSDP_ADDR)

        deadline = time.time() + timeout_seconds
        found: dict[str, DiscoveredBridge] = {}

        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            packet = data.decode("utf-8", "ignore")
            headers = _parse_httpish_headers(packet)

            location = headers.get("location")
            ip = _ip_from_location(location) if location else None
            if not ip:
                ip = addr[0]

            server = headers.get("server", "")
            st_hdr = headers.get("st", "")
            looks = ("ipbridge" in server.lower()) or ("ipbridge" in packet.lower()) or (
                "urn:schemas-upnp-org:device:basic:1" in st_hdr.lower()
            )
            if not looks:
                continue

            found[ip] = DiscoveredBridge(
                ip=ip,
                source="ssdp",
                location=location,
                raw={"headers": headers, "from": addr[0]},
            )

        return list(found.values())
    finally:
        sock.close()


def _fetch_description(location: str, *, timeout: float = 2.0) -> str | None:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(location)
            if resp.status_code != 200:
                return None
            return resp.text
    except Exception:
        return None


def enrich_with_description(bridge: DiscoveredBridge) -> DiscoveredBridge:
    if not bridge.location:
        return bridge
    xml_text = _fetch_description(bridge.location)
    if not xml_text or not _looks_like_hue_description(xml_text):
        return bridge
    fields = _extract_upnp_fields(xml_text)
    return DiscoveredBridge(
        ip=bridge.ip,
        source=bridge.source,
        location=bridge.location,
        udn=fields.get("udn"),
        model=fields.get("model"),
        friendly_name=fields.get("friendly_name"),
        raw=bridge.raw,
    )


def mdns_discover(*, timeout_seconds: float = 3.0) -> list[DiscoveredBridge]:
    # Optional dependency. Many environments (esp. Docker Desktop) will not support multicast well.
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except Exception:
        return []

    found: dict[str, DiscoveredBridge] = {}

    def on_service_state_change(zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange):
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1000)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        found[ip] = DiscoveredBridge(
            ip=ip,
            source="mdns",
            location=None,
            raw={"name": name, "port": info.port},
        )

    zc = Zeroconf()
    try:
        _ = ServiceBrowser(zc, "_hue._tcp.local.", handlers=[on_service_state_change])
        time.sleep(timeout_seconds)
    finally:
        zc.close()

    return list(found.values())


def ip_scan_description_xml(*, cidr: str, timeout: float = 0.4, concurrency: int = 64) -> list[DiscoveredBridge]:
    # Fallback (slow): scan for http://<ip>/description.xml
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in net.hosts()]
    found: list[DiscoveredBridge] = []
    import threading

    lock = threading.Lock()

    def worker(ip: str) -> None:
        url = f"http://{ip}/description.xml"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return
                if not _looks_like_hue_description(resp.text):
                    return
                fields = _extract_upnp_fields(resp.text)
                with lock:
                    found.append(
                        DiscoveredBridge(
                            ip=ip,
                            source="scan",
                            location=url,
                            udn=fields.get("udn"),
                            model=fields.get("model"),
                            friendly_name=fields.get("friendly_name"),
                        )
                    )
        except Exception:
            return

    # Simple bounded concurrency using threads (portable, avoids asyncio complexity for operators).
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(worker, hosts))

    # de-dupe by ip
    uniq: dict[str, DiscoveredBridge] = {b.ip: b for b in found}
    return list(uniq.values())


def _print_bridges(bridges: list[DiscoveredBridge], *, json_out: bool) -> None:
    if json_out:
        import json

        print(
            json.dumps(
                [
                    {
                        "ip": b.ip,
                        "source": b.source,
                        "location": b.location,
                        "udn": b.udn,
                        "model": b.model,
                        "friendlyName": b.friendly_name,
                    }
                    for b in bridges
                ],
                indent=2,
            )
        )
        return

    if not bridges:
        print("No Hue bridges discovered.")
        return

    for i, b in enumerate(bridges, start=1):
        label = b.friendly_name or b.model or "Hue Bridge"
        extra = []
        if b.source:
            extra.append(b.source)
        if b.location:
            extra.append("location")
        print(f"{i}) {b.ip} - {label} ({', '.join(extra)})")


def _maybe_set_gateway_host(
    *,
    gateway_url: str,
    token: str | None,
    api_key: str | None,
    bridges: list[DiscoveredBridge],
) -> None:
    if not token and not api_key:
        print("Skipping gateway host set: missing gateway credentials.", file=sys.stderr)
        return

    if not bridges:
        print("Skipping gateway host set: no discovered bridges.", file=sys.stderr)
        return

    if len(bridges) == 1:
        chosen = bridges[0]
    else:
        if not sys.stdin.isatty():
            print("Multiple bridges found; rerun with --json and choose one.", file=sys.stderr)
            return
        _print_bridges(bridges, json_out=False)
        while True:
            raw = input(f"Select bridge [1-{len(bridges)}]: ").strip()
            try:
                idx = int(raw)
                if 1 <= idx <= len(bridges):
                    chosen = bridges[idx - 1]
                    break
            except ValueError:
                pass
            print("Invalid selection.")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-API-Key"] = api_key or ""

    with httpx.Client(timeout=5.0) as client:
        resp = client.post(
            f"{gateway_url.rstrip('/')}/v1/actions",
            headers=headers,
            json={"action": "bridge.set_host", "args": {"bridgeHost": chosen.ip}},
        )
        if resp.status_code == 200:
            print(f"Gateway bridge host set to {chosen.ip}.")
        else:
            print(f"Failed to set gateway bridge host: HTTP {resp.status_code} {resp.text}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hue-gateway-discover")
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-mdns", action="store_true", help="Skip mDNS/zeroconf discovery")
    parser.add_argument("--no-ssdp", action="store_true", help="Skip SSDP/UPnP discovery")
    parser.add_argument("--enrich", action="store_true", help="Fetch and parse description.xml for metadata")
    parser.add_argument("--scan-cidr", help="Fallback: scan CIDR for http://<ip>/description.xml (slow)")
    parser.add_argument("--scan-timeout", type=float, default=0.4)
    parser.add_argument("--scan-concurrency", type=int, default=64)

    parser.add_argument("--set-gateway-host", action="store_true", help="Call gateway bridge.set_host for the chosen IP")
    parser.add_argument("--gateway-url", default=os.getenv("HUE_GATEWAY_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("HUE_GATEWAY_TOKEN"))
    parser.add_argument("--api-key", default=os.getenv("HUE_GATEWAY_API_KEY"))

    args = parser.parse_args(argv)

    bridges: list[DiscoveredBridge] = []
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures: list[concurrent.futures.Future[list[DiscoveredBridge]]] = []
        if not args.no_ssdp:
            futures.append(ex.submit(ssdp_discover, timeout_seconds=args.timeout_seconds))
        if not args.no_mdns:
            futures.append(ex.submit(mdns_discover, timeout_seconds=args.timeout_seconds))
        if args.scan_cidr:
            futures.append(
                ex.submit(
                    ip_scan_description_xml,
                    cidr=args.scan_cidr,
                    timeout=args.scan_timeout,
                    concurrency=args.scan_concurrency,
                )
            )
        for f in futures:
            try:
                bridges.extend(f.result())
            except Exception:
                continue

    # De-dupe by IP, prefer enriched/with location.
    by_ip: dict[str, DiscoveredBridge] = {}
    for b in bridges:
        prev = by_ip.get(b.ip)
        if not prev:
            by_ip[b.ip] = b
            continue
        if not prev.location and b.location:
            by_ip[b.ip] = b

    bridges = list(by_ip.values())
    if args.enrich:
        bridges = [enrich_with_description(b) for b in bridges]

    _print_bridges(bridges, json_out=args.json)

    if args.set_gateway_host:
        _maybe_set_gateway_host(
            gateway_url=args.gateway_url,
            token=args.token,
            api_key=args.api_key,
            bridges=bridges,
        )


if __name__ == "__main__":
    main()
