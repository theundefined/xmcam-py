"""Live-view helpers: RTSP URLs and JPEG snapshots.

These build on the anonymous-ONVIF data (see :mod:`xmcam.onvif`) and on the
camera's HTTP ``webcapture`` endpoint. They need the stream account, which you
either pass in or recover with :func:`xmcam.credentials.recover_credentials`.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from typing import List

from .exceptions import OnvifError
from .models import StreamUri


def rtsp_url(
    ip: str,
    username: str,
    password: str,
    channel: int = 0,
    stream: int = 0,
    port: int = 554,
) -> str:
    """Build the Xiongmai RTSP URL (credentials embedded, as the app uses it).

    ``stream`` 0 is the main (HD) stream, 1 the sub stream.
    """
    return (
        f"rtsp://{ip}:{port}/user={username}_password={password}"
        f"_channel={channel}_stream={stream}&onvif=0.sdp?real_stream"
    )


def snapshot_url(
    ip: str,
    username: str,
    password: str,
    channel: int = 0,
    port: int = 80,
) -> str:
    """Build the HTTP ``webcapture`` snapshot URL (port 80)."""
    query = urllib.parse.urlencode({"command": "snap", "channel": channel, "user": username, "password": password})
    return f"http://{ip}:{port}/webcapture.jpg?{query}"


def fetch_snapshot(
    ip: str,
    username: str,
    password: str,
    channel: int = 0,
    port: int = 80,
    timeout: float = 10.0,
) -> bytes:
    """Fetch a single JPEG frame from the camera and return the raw bytes."""
    url = snapshot_url(ip, username, password, channel=channel, port=port)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (local LAN)
            return resp.read()
    except Exception as exc:  # pragma: no cover - network path
        raise OnvifError(f"snapshot request to {ip} failed: {exc}") from exc


def get_live_streams(ip: str, port: int = 8899, timeout: float = 6.0) -> List[StreamUri]:
    """Return RTSP stream URIs (with embedded credentials) via anonymous ONVIF."""
    from . import onvif  # local import: keeps the package importable in pieces

    return onvif.get_stream_uris(ip, port=port, timeout=timeout)
