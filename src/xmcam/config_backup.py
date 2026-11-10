"""Export and import camera configuration blocks over DVRIP.

.. warning::
   BEST-EFFORT. Opcodes 1042 (config GET, same as ``get_info``) and 1040
   (config SET) and the request layout are NOT verified against real cameras.

   LIVE-TODO: verify 1040/1042 behaviour on real hardware before relying on it.

.. warning::
   :func:`import_config` WRITES to the camera. It changes live configuration
   (network, encoding, device identity, ...). Only use it deliberately, and
   never in tests against a real device; the test suite uses a fake socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

from .dvrip import DVRIPClient, OK_CODES

#: DVRIP opcode for config GET (best-effort, unverified live).
CONFIG_GET_MSGID = 1042
#: DVRIP opcode for config SET (best-effort, unverified live).
CONFIG_SET_MSGID = 1040

#: Representative config blocks. Some may answer 607 on a given model; those are skipped.
DEFAULT_CONFIG_BLOCKS = [
    "General",
    "Camera",
    "Encode",
    "NetWork.NetCommon",
    "Simplify.Encode",
    "AVEnc.VideoWidget",
    "General.Location",
]


def export_config(cam: DVRIPClient, blocks: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Fetch configuration blocks from the camera.

    :param blocks: Block names to fetch; defaults to :data:`DEFAULT_CONFIG_BLOCKS`.
    :returns: ``{block_name: block_value}`` for every block whose reply has an
        OK ``Ret``. Blocks answering anything else (e.g. 607) are skipped.
    """
    names = list(blocks) if blocks is not None else list(DEFAULT_CONFIG_BLOCKS)
    result: Dict[str, Any] = {}
    for name in names:
        reply = cam.send(CONFIG_GET_MSGID, {"Name": name, "SessionID": cam.session_hex})
        if not isinstance(reply, dict) or reply.get("Ret") not in OK_CODES:
            continue
        result[name] = reply.get(name, reply)
    return result


def save_config(
    cam: DVRIPClient,
    path: Union[str, Path],
    blocks: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Export configuration and write it to *path* as indented JSON.

    :returns: The exported dict (same as :func:`export_config`).
    """
    data = export_config(cam, blocks)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return data


def import_config(cam: DVRIPClient, data: Dict[str, Any]) -> Dict[str, Any]:
    """Write configuration blocks to the camera, one SET per block.

    .. warning::
       THIS WRITES TO THE CAMERA. Each entry of *data* is pushed live via
       opcode 1040. Wrong values can break network access or recording.

    :param data: ``{block_name: block_value}``, e.g. the output of
        :func:`export_config`.
    :returns: ``{block_name: Ret}`` as reported by the camera (``None`` when the
        reply carried no ``Ret``).
    """
    results: Dict[str, Any] = {}
    for name, value in data.items():
        body = {"Name": name, name: value, "SessionID": cam.session_hex}
        reply = cam.send(CONFIG_SET_MSGID, body)
        results[name] = reply.get("Ret") if isinstance(reply, dict) else None
    return results
