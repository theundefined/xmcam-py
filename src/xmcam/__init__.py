"""xmcam - local-first toolkit for Xiongmai (XM) / iCSee / XMeye IP cameras.

The package is organised into focused modules:

* :mod:`xmcam.discovery`   - find cameras on the LAN (WS-Discovery)
* :mod:`xmcam.onvif`       - anonymous ONVIF reads (device info, users, stream URIs)
* :mod:`xmcam.credentials` - recover the camera account from anonymous ONVIF
* :mod:`xmcam.dvrip`       - low-level DVRIP ("Sofia") client
* :mod:`xmcam.files`       - list the recording archive (OPFileQuery)
* :mod:`xmcam.playback`    - download/stream recordings (OPPlayBack)
* :mod:`xmcam.media`       - demux the XM container and remux to MP4
* :mod:`xmcam.live`        - live RTSP URLs and JPEG snapshots
* :mod:`xmcam.ptz`         - PTZ control (where supported)
* :mod:`xmcam.config_backup` - export/import camera configuration
* :mod:`xmcam.timesync`    - read/set the camera clock

Everything runs on the local network - no cloud, no internet.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .dvrip import DVRIP_PORT, DVRIPClient, OK_CODES, RET_MEANING, sofia_hash
from .exceptions import (
    DownloadError,
    LoginError,
    OnvifError,
    ProtocolError,
    XMCamError,
    XMConnectionError,
)
from .models import Camera, Credentials, DeviceInfo, Recording, StreamUri

# High-level modules and their most-used entry points.
from . import (  # noqa: E402
    config_backup,
    credentials,
    discovery,
    files,
    live,
    media,
    onvif,
    playback,
    ptz,
    timesync,
)
from .credentials import recover_credentials
from .discovery import discover
from .files import archive_span, find_newest, find_oldest, list_recordings, list_recordings_range
from .media import XmDemuxer, convert_raw, demux, have_ffmpeg
from .playback import download_recording, iter_recording

__all__ = [
    "__version__",
    # low-level
    "DVRIPClient",
    "DVRIP_PORT",
    "OK_CODES",
    "RET_MEANING",
    "sofia_hash",
    # models
    "Camera",
    "Credentials",
    "DeviceInfo",
    "Recording",
    "StreamUri",
    # exceptions
    "XMCamError",
    "XMConnectionError",
    "ProtocolError",
    "LoginError",
    "DownloadError",
    "OnvifError",
    # high-level entry points
    "discover",
    "recover_credentials",
    "list_recordings",
    "list_recordings_range",
    "find_oldest",
    "find_newest",
    "archive_span",
    "download_recording",
    "iter_recording",
    "demux",
    "XmDemuxer",
    "convert_raw",
    "have_ffmpeg",
    # modules
    "discovery",
    "onvif",
    "credentials",
    "files",
    "playback",
    "media",
    "live",
    "ptz",
    "config_backup",
    "timesync",
]

try:
    __version__ = _pkg_version("xmcam-py")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"
