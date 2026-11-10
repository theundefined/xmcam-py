"""Recover the stream account of a camera you own via anonymous ONVIF."""

from __future__ import annotations

from typing import Optional

from . import onvif
from .models import Credentials


def recover_credentials(ip: str, port: int = 8899, timeout: float = 6.0) -> Optional[Credentials]:
    """Return the first credentials embedded in a stream URI, or ``None``."""
    for su in onvif.get_stream_uris(ip, port, timeout):
        if su.username is not None and su.password is not None:
            return Credentials(username=su.username, password=su.password, source="onvif")
    return None
