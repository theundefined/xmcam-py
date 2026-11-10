"""WS-Discovery over ONVIF: find cameras on the local network.

A WS-Discovery *Probe* is sent as a UDP multicast datagram to
``239.255.255.250:3702``. Every ONVIF device on the segment answers with a
*ProbeMatch* datagram that contains its service addresses (``XAddrs``) and its
``Scopes`` (which often carry the MAC address and the hardware model).

The network I/O lives in :func:`discover`; the parsing lives in
:func:`camera_from_probe_match` so it can be tested without a network.

Everything here is local-only: no cloud, no internet.
"""

from __future__ import annotations

import html
import re
import socket
import time
import uuid
from typing import Optional

from .models import Camera

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 3702

# Ports and path fragments used to recognise ONVIF services.
_ONVIF_MARKER = "onvif"
# ONVIF served on port 8899 is the signature of Xiongmai (XM) firmware.
_XIONGMAI_MARKER = ":8899/onvif"

_PROBE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header><w:MessageID>uuid:{msg_id}</w:MessageID>"
    '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '<w:Action e:mustUnderstand="true">'
    "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>"
    "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
    "</e:Envelope>"
)

# Element text, tolerant of any namespace prefix (``d:XAddrs``, ``XAddrs``, ...).
_XADDRS_RE = re.compile(r"<(?:[\w.-]+:)?XAddrs>([^<]*)</", re.IGNORECASE)
_SCOPES_RE = re.compile(r"<(?:[\w.-]+:)?Scopes[^>]*>([^<]*)</", re.IGNORECASE)
# ``.../mac/aa:bb:cc:dd:ee:ff`` - colon or dash separated, any case.
_MAC_RE = re.compile(r"/mac/((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![0-9A-Za-z])")


def _normalise_mac(raw: str) -> str:
    """Return a MAC address as lowercase hex with colons (``aa:bb:cc:dd:ee:ff``)."""
    return raw.replace("-", ":").lower()


def _onvif_xaddr(xaddrs_text: str) -> Optional[str]:
    """Return the first ONVIF service URL from a whitespace-separated XAddrs value."""
    for url in xaddrs_text.split():
        if _ONVIF_MARKER in url.lower():
            return url
    return None


def camera_from_probe_match(src_ip: str, response_text: str) -> Optional[Camera]:
    """Parse one WS-Discovery ProbeMatch message into a :class:`Camera`.

    Args:
        src_ip: Source address of the UDP datagram (the camera's IP).
        response_text: The decoded XML body of the datagram.

    Returns:
        A :class:`Camera`, or ``None`` when the message carries no ONVIF
        service address (for example a Microsoft WSD device or other noise).
    """
    xaddrs_match = _XADDRS_RE.search(response_text)
    if not xaddrs_match:
        return None
    xaddrs_text = html.unescape(xaddrs_match.group(1))
    xaddr = _onvif_xaddr(xaddrs_text)
    if xaddr is None:
        return None

    scopes_match = _SCOPES_RE.search(response_text)
    scopes = html.unescape(scopes_match.group(1)).strip() if scopes_match else ""

    mac_match = _MAC_RE.search(scopes)
    mac = _normalise_mac(mac_match.group(1)) if mac_match else None

    return Camera(
        ip=src_ip,
        xaddr=xaddr,
        scopes=scopes,
        mac=mac,
        is_xiongmai=_XIONGMAI_MARKER in xaddr.lower(),
        raw={"xaddr": xaddr, "scopes": scopes},
    )


def discover(iface_ip: Optional[str] = None, timeout: float = 5.0) -> list[Camera]:
    """Find ONVIF cameras on the LAN with a WS-Discovery multicast probe.

    Args:
        iface_ip: Local IPv4 address of the interface to send the probe from.
            ``None`` lets the OS choose the default multicast interface.
        timeout: Seconds to listen for ProbeMatch replies.

    Returns:
        One :class:`Camera` per responding ONVIF device, deduplicated by source
        IP (the last reply wins) and sorted by IP address. Non-ONVIF responders
        are ignored.
    """
    probe = _PROBE.format(msg_id=uuid.uuid4()).encode("utf-8")
    found: dict[str, Camera] = {}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if iface_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
        sock.settimeout(1.0)
        # Probes are UDP and may be lost; send twice as the reference client does.
        for _ in range(2):
            sock.sendto(probe, (MULTICAST_ADDR, MULTICAST_PORT))

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            text = data.decode("utf-8", errors="replace")
            camera = camera_from_probe_match(addr[0], text)
            if camera is not None:
                found[addr[0]] = camera

    return [found[ip] for ip in sorted(found, key=_ip_sort_key)]


def _ip_sort_key(ip: str) -> tuple:
    """Sort dotted IPv4 addresses numerically; fall back to string order."""
    try:
        return (0, tuple(int(part) for part in ip.split(".")))
    except ValueError:
        return (1, (), ip)
