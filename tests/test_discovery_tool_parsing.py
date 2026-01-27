from hue_gateway.discover_tool import (
    _extract_upnp_fields,
    _ip_from_location,
    _looks_like_hue_description,
    _parse_httpish_headers,
)


def test_parse_httpish_headers_lowercases_keys():
    packet = "HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:Basic:1\r\nLOCATION: http://1.2.3.4/description.xml\r\n\r\n"
    headers = _parse_httpish_headers(packet)
    assert headers["st"] == "urn:schemas-upnp-org:device:Basic:1"
    assert headers["location"] == "http://1.2.3.4/description.xml"


def test_ip_from_location_extracts_hostname():
    assert _ip_from_location("http://192.168.1.2:80/description.xml") == "192.168.1.2"


def test_looks_like_hue_description_accepts_basic1_hue_metadata():
    xml = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
    <friendlyName>Philips hue (192.168.1.2)</friendlyName>
    <manufacturer>Signify Netherlands B.V.</manufacturer>
    <modelName>Philips hue bridge 2015</modelName>
  </device>
</root>
"""
    assert _looks_like_hue_description(xml) is True


def test_looks_like_hue_description_rejects_non_hue():
    xml = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
    <friendlyName>Some Other Device</friendlyName>
    <manufacturer>Acme</manufacturer>
    <modelName>Router</modelName>
  </device>
</root>
"""
    assert _looks_like_hue_description(xml) is False


def test_extract_upnp_fields_parses_default_namespace():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
    <friendlyName>Hue Bridge (192.168.1.29)</friendlyName>
    <manufacturer>Signify</manufacturer>
    <modelName>Philips hue bridge 2015</modelName>
    <UDN>uuid:abc</UDN>
  </device>
</root>
"""
    fields = _extract_upnp_fields(xml)
    assert fields["friendly_name"] == "Hue Bridge (192.168.1.29)"
    assert fields["model"] == "Philips hue bridge 2015"
    assert fields["udn"] == "uuid:abc"
