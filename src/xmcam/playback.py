"""Download recordings via the DVRIP ``OPPlayBack`` command.

Verified sequence (XM530 firmware): ``Claim`` (msgid 1424) -> ``DownloadStart``
(msgid 1420, no reply awaited) -> media frames with msgid 1426 until the camera
closes the stream -> best-effort ``Stop``.
"""

from __future__ import annotations

from typing import Callable, Iterator, Optional

from .dvrip import DVRIPClient, OK_CODES, ret_meaning
from .exceptions import DownloadError
from .models import TIME_FMT, Recording

CLAIM_MSGID = 1424
CONTROL_MSGID = 1420

#: msgid carried by media frames, per start action.
MEDIA_MSGID = {"DownloadStart": 1426, "Start": 1422}
DOWNLOAD_MEDIA_MSGID = MEDIA_MSGID["DownloadStart"]


def _times(rec: Recording) -> tuple:
    raw = rec.raw or {}
    start = raw.get("BeginTime") or (rec.begin_time.strftime(TIME_FMT) if rec.begin_time else None)
    end = raw.get("EndTime") or (rec.end_time.strftime(TIME_FMT) if rec.end_time else None)
    if not start or not end:
        raise DownloadError(f"recording {rec.filename!r} has no begin/end time")
    return start, end


def iter_recording(
    cam: DVRIPClient,
    recording: Recording,
    channel: int = 0,
    stream: str = "Main",
    idle_timeout: float = 20,
) -> Iterator[bytes]:
    """Claim and start a download, yielding each media payload as it arrives.

    Ends when the camera closes the stream (or nothing arrives for
    *idle_timeout* seconds). ``Stop`` is sent in a ``finally`` block.
    """
    start, end = _times(recording)
    param = {
        "Channel": channel,
        "CombinMode": "NONE",
        "PlayMode": "ByName",
        "FileName": recording.filename,
        "StreamType": stream,
        "TransMode": "TCP",
        "Value": 0,
    }

    def body(action: str) -> dict:
        return {
            "Name": "OPPlayBack",
            "SessionID": cam.session_hex,
            "OPPlayBack": {"Action": action, "StartTime": start, "EndTime": end, "Parameter": param},
        }

    reply = cam.send(CLAIM_MSGID, body("Claim")) or {}
    ret = reply.get("Ret")
    if ret not in OK_CODES:
        hint = " (channel busy: relogin before the next file)" if ret == 103 else ""
        raise DownloadError(
            f"OPPlayBack Claim failed for {recording.filename} " f"(Ret={ret}: {ret_meaning(ret)}){hint}"
        )

    sock = cam.socket
    try:
        if sock is not None:
            sock.settimeout(idle_timeout)
        cam.send(CONTROL_MSGID, body("DownloadStart"), wait_response=False)
        while True:
            msgid, payload = cam.read_frame()
            if msgid is None:
                break
            if msgid == DOWNLOAD_MEDIA_MSGID and payload:
                yield payload
    finally:
        try:
            cam.send(CONTROL_MSGID, body("Stop"), wait_response=False)
        except Exception:
            pass
        try:
            if cam.socket is not None:
                cam.socket.settimeout(cam.timeout)
        except Exception:
            pass


def download_recording(
    cam: DVRIPClient,
    recording: Recording,
    out_path,
    progress: Optional[Callable[[int], None]] = None,
    channel: int = 0,
    stream: str = "Main",
    idle_timeout: float = 20,
) -> int:
    """Download *recording* to *out_path*; return the number of bytes written."""
    total = 0
    with open(out_path, "wb") as fh:
        for chunk in iter_recording(cam, recording, channel, stream, idle_timeout):
            fh.write(chunk)
            total += len(chunk)
            if progress is not None:
                progress(total)
    return total
