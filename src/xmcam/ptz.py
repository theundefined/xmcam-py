"""PTZ (pan/tilt/zoom) control over DVRIP command 1400 (``OPPTZControl``).

.. warning::
   BEST-EFFORT. The request layout below follows the public XMeye/DVRIP
   conventions and has NOT been verified against a real PTZ camera. Many
   fixed (non-PTZ) cameras answer ``Ret 607`` (command not supported); that is
   treated as "unsupported" and returned to the caller rather than raised.

   LIVE-TODO: verify opcode 1400 and the ``OPPTZControl`` parameter layout
   against a real PTZ camera before relying on it.
"""

from __future__ import annotations

from typing import Dict

from .dvrip import DVRIPClient, OK_CODES
from .exceptions import XMCamError

#: DVRIP opcode for ``OPPTZControl`` (best-effort, unverified live).
PTZ_MSGID = 1400

#: Ret code meaning "command not supported on this camera/account".
RET_UNSUPPORTED = 607

#: Friendly direction name -> DVRIP ``OPPTZControl`` command name.
PTZ_COMMANDS: Dict[str, str] = {
    "up": "DirectionUp",
    "down": "DirectionDown",
    "left": "DirectionLeft",
    "right": "DirectionRight",
    "zoomin": "ZoomTile",
    "zoomout": "ZoomWide",
}


def ptz_move(
    cam: DVRIPClient,
    direction: str,
    channel: int = 0,
    step: int = 4,
    stop: bool = False,
) -> dict:
    """Send one PTZ movement command and return the camera's reply.

    :param cam: A logged-in :class:`DVRIPClient`.
    :param direction: One of the keys of :data:`PTZ_COMMANDS`.
    :param channel: Video channel index.
    :param step: Movement step sent to the camera.
    :param stop: When true, the step is forced to ``0``, which is how this
        module requests a stop (see :func:`ptz_stop`). Best-effort: the stop
        semantics are unverified on real hardware.
    :returns: The parsed reply dict. A ``Ret 607`` reply is returned, not raised.
    :raises ValueError: If *direction* is unknown (no request is sent).
    :raises XMCamError: If the camera answers with a ``Ret`` that is neither
        an OK code nor 607.
    """
    if direction not in PTZ_COMMANDS:
        raise ValueError(f"unknown PTZ direction {direction!r}; expected one of {sorted(PTZ_COMMANDS)}")

    command = PTZ_COMMANDS[direction]
    effective_step = 0 if stop else step
    body = {
        "Name": "OPPTZControl",
        "OPPTZControl": {
            "Command": command,
            "Parameter": {
                "AUX": {"Number": 0, "Status": "On"},
                "Channel": channel,
                "MenuOpt": "Enter",
                "POINT": {"bottom": 0, "left": 0, "right": 0, "top": 0},
                "Preset": -1,
                "Step": effective_step,
                "Type": "Direction",
            },
        },
    }
    reply = cam.send(PTZ_MSGID, body) or {}
    ret = reply.get("Ret")
    if ret not in OK_CODES and ret != RET_UNSUPPORTED:
        raise XMCamError(f"PTZ {direction} failed on {cam.host}: Ret={ret}")
    return reply


def ptz_stop(cam: DVRIPClient, direction: str, channel: int = 0) -> dict:
    """Stop movement along *direction*. Equivalent to ``ptz_move(..., step=0)``."""
    return ptz_move(cam, direction, channel=channel, step=0)
