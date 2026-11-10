"""Typed data structures returned across the public API.

These are plain dataclasses so they are easy to serialise, compare in tests and
inspect in a REPL. Every structure keeps the original camera payload in a
``raw`` field so callers never lose information the typed view does not surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def parse_file_length(value: Any) -> int:
    """Return a recording size in **bytes**.

    Xiongmai reports ``FileLength`` in *kilobytes*, usually as a hex string such
    as ``"0x0000c3cc"`` but occasionally as a plain integer. The value is
    multiplied by 1024 to obtain bytes. Unparseable values yield ``0``.
    """
    try:
        if isinstance(value, str) and value.lower().startswith("0x"):
            kb = int(value, 16)
        else:
            kb = int(value)
    except (TypeError, ValueError):
        return 0
    return kb * 1024


def _parse_time(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    for fmt in (TIME_FMT, "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Camera:
    """A camera discovered on the LAN via WS-Discovery."""

    ip: str
    xaddr: str = ""
    scopes: str = ""
    mac: Optional[str] = None
    is_xiongmai: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class Credentials:
    """A username/password pair recovered from the camera."""

    username: str
    password: str
    source: str = "onvif"  # where the pair came from, e.g. "onvif" or "manual"


@dataclass
class DeviceInfo:
    """Basic identity of a camera (from ONVIF ``GetDeviceInformation`` or DVRIP)."""

    manufacturer: str = ""
    model: str = ""
    firmware: str = ""
    serial: str = ""
    hardware_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class StreamUri:
    """A live-stream or snapshot endpoint, optionally with embedded credentials."""

    uri: str
    kind: str = "rtsp"  # "rtsp" or "snapshot"
    profile: str = ""
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class Recording:
    """One recording segment stored on the camera's SD card or disk."""

    filename: str
    begin_time: Optional[datetime]
    end_time: Optional[datetime]
    length_bytes: int = 0
    channel: int = 0
    disk_no: Any = None
    serial_no: Any = None
    stream_type: str = "Main"
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, entry: dict, channel: int = 0, stream_type: str = "Main") -> "Recording":
        """Build a :class:`Recording` from a raw ``OPFileQuery`` list entry."""
        return cls(
            filename=entry.get("FileName", ""),
            begin_time=_parse_time(entry.get("BeginTime")),
            end_time=_parse_time(entry.get("EndTime")),
            length_bytes=parse_file_length(entry.get("FileLength", 0)),
            channel=channel,
            disk_no=entry.get("DiskNo"),
            serial_no=entry.get("SerialNo"),
            stream_type=stream_type,
            raw=entry,
        )

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.begin_time and self.end_time:
            return (self.end_time - self.begin_time).total_seconds()
        return None
