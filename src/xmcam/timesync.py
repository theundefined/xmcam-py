"""Read and set the camera clock.

Reading uses DVRIP ``OPTimeQuery`` (1453) when the firmware answers it, and
otherwise falls back to ONVIF ``GetSystemDateAndTime`` (reliable, verified on
XM530 / XM535). Setting the clock uses DVRIP ``OPTimeSetting`` (1450).

.. warning::
   The DVRIP time opcodes (1450 set, 1453 query) are best-effort; 1453 returns
   nothing on the cameras tested, which is why :func:`get_time` falls back to
   ONVIF. ``set_time`` (1450) is not exercised against live hardware here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .dvrip import DVRIPClient, OK_CODES
from .exceptions import XMCamError

#: DVRIP opcode for time query (best-effort, unverified live).
TIME_QUERY_MSGID = 1453
#: DVRIP opcode for time set (best-effort, unverified live).
TIME_SET_MSGID = 1450

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_time(cam: DVRIPClient, onvif_port: int = 8899) -> Optional[datetime]:
    """Return the camera's current clock, or ``None`` if it cannot be read.

    Tries the DVRIP ``OPTimeQuery`` first, then falls back to ONVIF
    ``GetSystemDateAndTime`` (which the tested firmwares answer reliably).
    """
    reply = cam.send(TIME_QUERY_MSGID, {"Name": "OPTimeQuery", "SessionID": cam.session_hex})
    if isinstance(reply, dict):
        raw = reply.get("OPTimeQuery")
        if isinstance(raw, str):
            try:
                return datetime.strptime(raw.strip(), _TIME_FORMAT)
            except ValueError:
                pass
    # DVRIP did not answer; use anonymous ONVIF. Reading the clock is
    # best-effort, so any failure here just yields None rather than raising.
    try:
        from . import onvif

        return onvif.get_system_time(cam.host, port=onvif_port)
    except Exception:  # noqa: BLE001 - best-effort read
        return None


def set_time(cam: DVRIPClient, when: Optional[datetime] = None) -> dict:
    """Set the camera clock to *when* (local naive time; defaults to now).

    :returns: The parsed reply dict.
    :raises XMCamError: If the camera's ``Ret`` is not an OK code.
    """
    if when is None:
        when = datetime.now()
    body = {
        "Name": "OPTimeSetting",
        "OPTimeSetting": when.strftime(_TIME_FORMAT),
        "SessionID": cam.session_hex,
    }
    reply = cam.send(TIME_SET_MSGID, body) or {}
    ret = reply.get("Ret")
    if ret not in OK_CODES:
        raise XMCamError(f"time set failed on {cam.host}: Ret={ret}")
    return reply


def sync_to_host(cam: DVRIPClient) -> dict:
    """Set the camera clock to the host's current local time."""
    return set_time(cam, datetime.now())
