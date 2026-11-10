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

__version__ = "0.1.0"

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
from .files import list_recordings, list_recordings_range
from .media import convert_raw, demux, have_ffmpeg
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
    "download_recording",
    "iter_recording",
    "demux",
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
