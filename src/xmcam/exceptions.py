"""Exception hierarchy for :mod:`xmcam`.

All library errors derive from :class:`XMCamError`, so callers can catch the
whole family with a single ``except XMCamError``.
"""

from __future__ import annotations


class XMCamError(Exception):
    """Base class for every error raised by this library."""


class XMConnectionError(XMCamError):
    """A TCP/UDP connection to the camera could not be established or was lost."""


class ProtocolError(XMCamError):
    """A reply could not be parsed or violated the expected DVRIP framing."""


class LoginError(XMCamError):
    """Authentication to the DVRIP service failed.

    :param ret: The raw ``Ret`` code returned by the camera, if any.
    """

    def __init__(self, message: str, ret: int | None = None) -> None:
        super().__init__(message)
        self.ret = ret


class DownloadError(XMCamError):
    """A recording could not be claimed or streamed from the camera."""


class OnvifError(XMCamError):
    """An anonymous ONVIF request failed or returned a SOAP fault."""
