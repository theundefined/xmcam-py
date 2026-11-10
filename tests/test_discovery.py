"""Offline tests for WS-Discovery parsing and the discover() socket loop.

No real network traffic happens here: the XML samples are synthetic, use the
TEST-NET-1 range (192.0.2.0/24) and fake MAC addresses, and the socket is
replaced with a scripted fake.
"""

from __future__ import annotations

import socket

import pytest

from xmcam import discovery
from xmcam.discovery import camera_from_probe_match, discover
from xmcam.models import Camera

FAKE_MAC = "02:00:00:00:00:01"


def _probe_match(xaddrs: str, scopes: str) -> str:
    """Build a minimal WS-Discovery ProbeMatch envelope."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
        "<SOAP-ENV:Header><wsa:Action>"
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches"
        "</wsa:Action></SOAP-ENV:Header>"
        "<SOAP-ENV:Body><wsd:ProbeMatches><wsd:ProbeMatch>"
        "<wsa:EndpointReference><wsa:Address>urn:uuid:00000000-0000-0000-0000-000000000000"
        "</wsa:Address></wsa:EndpointReference>"
        "<wsd:Types>dn:NetworkVideoTransmitter</wsd:Types>"
        f"<wsd:Scopes>{scopes}</wsd:Scopes>"
        f"<wsd:XAddrs>{xaddrs}</wsd:XAddrs>"
        "<wsd:MetadataVersion>1</wsd:MetadataVersion>"
        "</wsd:ProbeMatch></wsd:ProbeMatches></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


XIONGMAI_SCOPES = (
    "onvif://www.onvif.org/type/video_encoder "
    "onvif://www.onvif.org/hardware/IPC-model "
    "onvif://www.onvif.org/name/IPCamera "
    f"onvif://www.onvif.org/mac/{FAKE_MAC} "
    "onvif://www.onvif.org/Profile/T"
)


def test_xiongmai_sample_is_fully_parsed():
    xml = _probe_match("http://192.0.2.10:8899/onvif/device_service", XIONGMAI_SCOPES)

    camera = camera_from_probe_match("192.0.2.10", xml)

    assert isinstance(camera, Camera)
    assert camera.ip == "192.0.2.10"
    assert camera.xaddr == "http://192.0.2.10:8899/onvif/device_service"
    assert camera.mac == FAKE_MAC
    assert camera.is_xiongmai is True
    assert "onvif://www.onvif.org/Profile/T" in camera.scopes
    assert camera.raw == {"xaddr": camera.xaddr, "scopes": camera.scopes}


def test_onvif_on_port_80_is_not_flagged_as_xiongmai():
    xml = _probe_match(
        "http://192.0.2.11/onvif/device_service",
        "onvif://www.onvif.org/name/GenericCam onvif://www.onvif.org/hardware/X1",
    )

    camera = camera_from_probe_match("192.0.2.11", xml)

    assert camera is not None
    assert camera.ip == "192.0.2.11"
    assert camera.xaddr == "http://192.0.2.11/onvif/device_service"
    assert camera.is_xiongmai is False
    assert camera.mac is None


def test_microsoft_wsd_device_without_onvif_is_skipped():
    xml = _probe_match(
        "http://192.0.2.20:5357/0123abcd/",
        "http://schemas.xmlsoap.org/ws/2005/04/discovery/Printer",
    )

    assert camera_from_probe_match("192.0.2.20", xml) is None


def test_mac_is_normalised_to_lowercase_colons():
    scopes = "onvif://www.onvif.org/mac/02-AB-CD-EF-01-23"
    xml = _probe_match("http://192.0.2.12/onvif/device_service", scopes)

    camera = camera_from_probe_match("192.0.2.12", xml)

    assert camera is not None
    assert camera.mac == "02:ab:cd:ef:01:23"


def test_onvif_url_is_picked_from_multiple_xaddrs():
    xaddrs = "http://192.0.2.13:5357/wsd/ http://192.0.2.13:8899/onvif/device_service"
    xml = _probe_match(xaddrs, XIONGMAI_SCOPES)

    camera = camera_from_probe_match("192.0.2.13", xml)

    assert camera is not None
    assert camera.xaddr == "http://192.0.2.13:8899/onvif/device_service"
    assert camera.is_xiongmai is True


def test_non_xml_garbage_returns_none():
    assert camera_from_probe_match("192.0.2.14", "not xml at all") is None
    assert camera_from_probe_match("192.0.2.14", "") is None


class _FakeUdpSocket:
    """Scripted stand-in for a UDP socket used by discover()."""

    script: list = []
    sent: list = []

    def __init__(self, *args, **kwargs):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def setsockopt(self, *args):
        pass

    def settimeout(self, value):
        pass

    def sendto(self, data, addr):
        type(self).sent.append((data, addr))
        return len(data)

    def recvfrom(self, bufsize):
        if type(self).script:
            item = type(self).script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise socket.timeout()


def test_discover_collects_onvif_devices_and_dedupes(monkeypatch):
    onvif_xml = _probe_match("http://192.0.2.10:8899/onvif/device_service", XIONGMAI_SCOPES).encode()
    old_xml = _probe_match("http://192.0.2.30/onvif/device_service", "onvif://www.onvif.org/name/Old").encode()
    wsd_xml = _probe_match(
        "http://192.0.2.40:5357/wsd/", "http://schemas.xmlsoap.org/ws/2005/04/discovery/Printer"
    ).encode()

    _FakeUdpSocket.script = [
        (onvif_xml, ("192.0.2.10", 3702)),
        (wsd_xml, ("192.0.2.40", 3702)),
        (old_xml, ("192.0.2.30", 3702)),
        # Same camera answering again: the later reply should replace the first.
        (onvif_xml, ("192.0.2.10", 3702)),
    ]
    _FakeUdpSocket.sent = []
    monkeypatch.setattr(discovery.socket, "socket", _FakeUdpSocket)

    cameras = discover(timeout=0.05)

    assert [c.ip for c in cameras] == ["192.0.2.10", "192.0.2.30"]
    assert cameras[0].is_xiongmai is True
    assert cameras[0].mac == FAKE_MAC
    assert cameras[1].is_xiongmai is False
    # Two probes go to the WS-Discovery multicast group.
    assert len(_FakeUdpSocket.sent) == 2
    assert all(addr == ("239.255.255.250", 3702) for _, addr in _FakeUdpSocket.sent)


@pytest.mark.parametrize("ip", ["192.0.2.2", "192.0.2.10"])
def test_discover_returns_empty_list_when_nobody_answers(monkeypatch, ip):
    _FakeUdpSocket.script = []
    _FakeUdpSocket.sent = []
    monkeypatch.setattr(discovery.socket, "socket", _FakeUdpSocket)

    assert discover(iface_ip=ip, timeout=0.05) == []
