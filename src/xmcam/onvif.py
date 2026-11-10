"""Anonymous ONVIF client for Xiongmai (XM / iCSee) cameras.

These cameras answer ONVIF requests without WS-Security on port 8899. The
``GetStreamUri`` reply embeds the stream account in plaintext inside the RTSP
URL, e.g. ``rtsp://IP:554/user=<LOGIN>_password=<PASSWORD>_channel=0_stream=0&onvif=0.sdp``.
This module parses the replies with regular expressions (stdlib only) and is
intended for cameras you own.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from .exceptions import OnvifError
from .models import DeviceInfo, StreamUri

DEVICE_NS = 'xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
MEDIA_NS = 'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" ' 'xmlns:tt="http://www.onvif.org/ver10/schema"'

# Primary pattern anchors the password on the following key (so a password
# that itself contains "_" is not truncated); the fallback is the loose form.
_CRED_RE = re.compile(r"user=(.*?)_password=(.*?)_(?:channel|stream|proto|real)=")
_CRED_RE_LOOSE = re.compile(r"user=([^_]*)_password=([^_]*)")


def _envelope(body: str, body_ns: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body {body_ns}>{body}</s:Body></s:Envelope>"
    )


def soap_post(url: str, body: str, body_ns: str = "", timeout: float = 6.0) -> str:
    """POST a SOAP 1.2 envelope and return the response text.

    Transport errors and HTTP errors without a SOAP body raise
    :class:`~xmcam.exceptions.OnvifError`. Tests monkeypatch this function.
    """
    data = _envelope(body, body_ns).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            text = ""
        raise OnvifError(f"HTTP {exc.code} from {url}: {text[:200]}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OnvifError(f"ONVIF request to {url} failed: {exc}") from exc


def _check_fault(text: str) -> None:
    if re.search(r"<(?:\w+:)?Fault[\s>]", text):
        reason = re.search(r"<(?:\w+:)?Text[^>]*>([^<]*)<", text)
        raise OnvifError("SOAP fault: " + (reason.group(1) if reason else "unknown"))


def _call(url: str, body: str, ns: str, timeout: float) -> str:
    text = soap_post(url, body, ns, timeout)
    _check_fault(text)
    return text


def _tag(text: str, name: str) -> str:
    m = re.search(rf"<(?:\w+:)?{name}(?:\s[^>]*)?>([^<]*)</(?:\w+:)?{name}>", text)
    return m.group(1).strip() if m else ""


def _device_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}/onvif/device_service"


def _media_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}/onvif/media_service"


def parse_rtsp_credentials(uri: str) -> Optional[tuple[str, str]]:
    """Return ``(user, password)`` from a ``user=..._password=..._`` URL, else ``None``."""
    m = _CRED_RE.search(uri or "") or _CRED_RE_LOOSE.search(uri or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def get_device_information(ip: str, port: int = 8899, timeout: float = 6.0) -> DeviceInfo:
    text = _call(_device_url(ip, port), "<tds:GetDeviceInformation/>", DEVICE_NS, timeout)
    raw = {key: _tag(text, key) for key in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId")}
    return DeviceInfo(
        manufacturer=raw["Manufacturer"],
        model=raw["Model"],
        firmware=raw["FirmwareVersion"],
        serial=raw["SerialNumber"],
        hardware_id=raw["HardwareId"],
        raw=raw,
    )


def get_system_time(ip: str, port: int = 8899, timeout: float = 6.0) -> Optional[datetime]:
    """Return the camera clock via ONVIF ``GetSystemDateAndTime`` (anonymous).

    Prefers ``LocalDateTime`` and falls back to ``UTCDateTime``. Returns ``None``
    if neither block can be parsed. This is more reliable than the DVRIP time
    query, which several firmwares do not answer.
    """
    text = _call(_device_url(ip, port), "<tds:GetSystemDateAndTime/>", DEVICE_NS, timeout)
    for block_name in ("LocalDateTime", "UTCDateTime"):
        block_match = re.search(rf"<(?:\w+:)?{block_name}\b.*?</(?:\w+:)?{block_name}>", text, re.S)
        if not block_match:
            continue
        block = block_match.group(0)

        def _num(tag: str) -> Optional[int]:
            m = re.search(rf"<(?:\w+:)?{tag}>(\d+)</", block)
            return int(m.group(1)) if m else None

        year, month, day = _num("Year"), _num("Month"), _num("Day")
        hour, minute, second = _num("Hour"), _num("Minute"), _num("Second")
        if year is None or month is None or day is None:
            continue
        try:
            return datetime(year, month, day, hour or 0, minute or 0, second or 0)
        except ValueError:
            continue
    return None


def get_users(ip: str, port: int = 8899, timeout: float = 6.0) -> list[dict]:
    text = _call(_device_url(ip, port), "<tds:GetUsers/>", DEVICE_NS, timeout)
    users = []
    for block in re.findall(r"<(?:\w+:)?User[\s>].*?</(?:\w+:)?User>", text, re.S):
        users.append({"Username": _tag(block, "Username"), "UserLevel": _tag(block, "UserLevel")})
    return users


def get_services(ip: str, port: int = 8899, timeout: float = 6.0) -> list[str]:
    body = "<tds:GetServices><tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>"
    text = _call(_device_url(ip, port), body, DEVICE_NS, timeout)
    return re.findall(r"<(?:\w+:)?Namespace>([^<]+)</(?:\w+:)?Namespace>", text)


def _profile_tokens(ip: str, port: int, timeout: float) -> list[str]:
    text = _call(_media_url(ip, port), "<trt:GetProfiles/>", MEDIA_NS, timeout)
    tokens: list[str] = []
    for tok in re.findall(r'(?:Profiles|Profile)[^>]*token="([^"]+)"', text):
        if tok not in tokens:
            tokens.append(tok)
    return tokens


def get_stream_uris(ip: str, port: int = 8899, timeout: float = 6.0) -> list[StreamUri]:
    out: list[StreamUri] = []
    for tok in _profile_tokens(ip, port, timeout):
        body = (
            "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
            f"<trt:ProfileToken>{tok}</trt:ProfileToken></trt:GetStreamUri>"
        )
        text = _call(_media_url(ip, port), body, MEDIA_NS, timeout)
        m = re.search(r"<(?:\w+:)?Uri>([^<]+)</(?:\w+:)?Uri>", text)
        if not m:
            continue
        uri = m.group(1).strip().replace("&amp;", "&")
        su = StreamUri(uri=uri, kind="rtsp", profile=tok)
        creds = parse_rtsp_credentials(uri)
        if creds:
            su.username, su.password = creds
        out.append(su)
    return out


def get_snapshot_uris(ip: str, port: int = 8899, timeout: float = 6.0) -> list[StreamUri]:
    out: list[StreamUri] = []
    for tok in _profile_tokens(ip, port, timeout):
        body = f"<trt:GetSnapshotUri><trt:ProfileToken>{tok}</trt:ProfileToken></trt:GetSnapshotUri>"
        text = _call(_media_url(ip, port), body, MEDIA_NS, timeout)
        m = re.search(r"<(?:\w+:)?Uri>([^<]+)</(?:\w+:)?Uri>", text)
        if m:
            out.append(StreamUri(uri=m.group(1).strip().replace("&amp;", "&"), kind="snapshot", profile=tok))
    return out
