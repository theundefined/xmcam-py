"""Recording listing via the DVRIP ``OPFileQuery`` command (msgid 1440)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Union

from .dvrip import DVRIPClient, OK_CODES
from .exceptions import ProtocolError
from .models import TIME_FMT, Recording

FILE_QUERY_MSGID = 1440

#: ``StreamType`` bit masks used by OPFileQuery.
STREAM_MASKS = {"Main": "0x00000000", "Sub": "0x00000001"}

#: Maximum number of entries the firmware returns for a single OPFileQuery.
#: Verified at 64 on XM530 / ONVIF 21.06; wider windows must be paginated.
OPFILEQUERY_PAGE_LIMIT = 64

TimeLike = Union[str, datetime]


def _to_datetime(value: TimeLike) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, TIME_FMT)


def _to_str(value: TimeLike) -> str:
    if isinstance(value, datetime):
        return value.strftime(TIME_FMT)
    return value


def list_recordings(
    cam: DVRIPClient,
    start: TimeLike,
    end: TimeLike,
    channel: int = 0,
    filetype: str = "h264",
    stream: str = "Main",
) -> List[Recording]:
    """Query the camera for recordings between *start* and *end* (one query)."""
    mask = STREAM_MASKS.get(stream, STREAM_MASKS["Main"])
    reply = cam.send(
        FILE_QUERY_MSGID,
        {
            "Name": "OPFileQuery",
            "OPFileQuery": {
                "BeginTime": _to_str(start),
                "EndTime": _to_str(end),
                "Channel": channel,
                "DriverTypeMask": "0x0000FFFF",
                "Event": "*",
                "Type": filetype,
                "StreamType": mask,
            },
        },
    )
    reply = reply or {}
    ret = reply.get("Ret")
    if ret not in OK_CODES:
        raise ProtocolError(f"OPFileQuery failed (Ret={ret})")
    entries = reply.get("OPFileQuery") or []
    out: List[Recording] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("FileName"):
            continue
        out.append(Recording.from_dict(entry, channel=channel, stream_type=stream))
    return out


def list_recordings_range(
    cam: DVRIPClient,
    start: TimeLike,
    end: TimeLike,
    channel: int = 0,
    filetype: str = "h264",
    stream: str = "Main",
    page_limit: int = OPFILEQUERY_PAGE_LIMIT,
) -> List[Recording]:
    """List recordings between *start* and *end*, paginating over time.

    A single ``OPFileQuery`` returns at most ~64 entries, so a wide window on a
    continuously-recording camera would be silently truncated. This walks the
    window forward: each page starts at the end time of the last entry of the
    previous page, and stops once a page comes back shorter than *page_limit*
    (nothing left) or yields no new entries. Results are de-duplicated on
    ``(filename, begin_time)`` and returned in chronological order.
    """
    start_dt = _to_datetime(start)
    end_dt = _to_datetime(end)
    seen = set()
    out: List[Recording] = []
    cursor = start_dt

    while cursor < end_dt:
        chunk = list_recordings(cam, cursor, end_dt, channel, filetype, stream)
        if not chunk:
            break
        new = 0
        max_end = cursor
        for rec in chunk:
            key = (rec.filename, rec.begin_time)
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
            new += 1
            edge = rec.end_time or rec.begin_time
            if edge and edge > max_end:
                max_end = edge
        # Stop if the page was not full (nothing left), nothing new arrived, or
        # the cursor cannot advance (guards against an infinite loop).
        if new == 0 or len(chunk) < page_limit or max_end <= cursor or max_end >= end_dt:
            break
        cursor = max_end

    out.sort(key=lambda r: (r.begin_time or datetime.min, r.filename))
    return out


def find_oldest(
    cam: DVRIPClient,
    lookback_days: int = 90,
    channel: int = 0,
    filetype: str = "h264",
    stream: str = "Main",
) -> Optional[Recording]:
    """Return the oldest recording still on the camera (within *lookback_days*).

    A single ``OPFileQuery`` from ``now - lookback_days`` returns the earliest
    page of results, so the oldest recording is just the first of them -- no need
    to enumerate the whole archive.
    """
    end = datetime.now()
    start = end - timedelta(days=lookback_days)
    page = list_recordings(cam, start, end, channel=channel, filetype=filetype, stream=stream)
    page = [r for r in page if r.begin_time is not None]
    return min(page, key=lambda r: r.begin_time) if page else None  # type: ignore[arg-type,return-value]


def find_newest(
    cam: DVRIPClient,
    lookback_hours: int = 48,
    channel: int = 0,
    filetype: str = "h264",
    stream: str = "Main",
) -> Optional[Recording]:
    """Return the most recent recording on the camera (within *lookback_hours*)."""
    end = datetime.now()
    start = end - timedelta(hours=lookback_hours)
    recs = list_recordings_range(cam, start, end, channel=channel, filetype=filetype, stream=stream)
    recs = [r for r in recs if r.begin_time is not None]
    return max(recs, key=lambda r: r.begin_time) if recs else None  # type: ignore[arg-type,return-value]


def archive_span(
    cam: DVRIPClient,
    lookback_days: int = 90,
    channel: int = 0,
    filetype: str = "h264",
    stream: str = "Main",
) -> tuple:
    """Return ``(oldest, newest)`` recordings, or ``(None, None)`` if empty."""
    oldest = find_oldest(cam, lookback_days, channel, filetype, stream)
    newest = find_newest(cam, 48, channel, filetype, stream)
    return oldest, newest
