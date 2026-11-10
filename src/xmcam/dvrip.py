"""Minimal, dependency-free client for the Xiongmai DVRIP ("Sofia") protocol.

DVRIP is the native TCP protocol (default port 34567) spoken by Xiongmai-based
cameras and the XMeye / iCSee apps. This module implements just enough of it to
log in and exchange JSON commands and binary media frames, with no third-party
dependencies.

Wire format (verified live on XM530 / ONVIF 21.06 firmware):

* Every message starts with a 20-byte little-endian header followed by a
  payload. The header is ``struct "BB2xIIBBHI"`` =
  ``head(0xFF), version, 2x reserved, session(u32), sequence(u32),
  total(u8), cur(u8), msgid(u16), length(u32)``.
* JSON payloads are UTF-8 and terminated with ``\\x0a\\x00``.
* Large JSON replies are split across several packets; ``total``/``cur`` in the
  header drive reassembly.
* Binary media (during playback/download) arrives as a stream of these frames
  whose ``msgid`` identifies the stream; the camera closes the socket to signal
  the end.

The login quirk this client exists to handle: some firmware accepts the
password as **plaintext** rather than the classic "Sofia" MD5 hash, so
:meth:`DVRIPClient.login` tries the hash first and transparently falls back to
plaintext.
"""

from __future__ import annotations

import hashlib
import json
import socket
import struct
from typing import Any, Optional, Tuple

from .exceptions import LoginError, ProtocolError, XMConnectionError

DVRIP_PORT = 34567

#: ``Ret`` codes that indicate success.
OK_CODES: tuple = (100, 515)

#: Human-readable meaning of observed ``Ret`` codes (see the project knowledge base).
RET_MEANING = {
    100: "OK",
    101: "unknown error",
    102: "unauthorised / bad sequence / missing SessionID",
    103: "playback channel busy (relogin before the next file)",
    104: "user already logged in",
    106: "wrong password or user (some firmware)",
    203: "wrong password",
    204: "account disabled",
    205: "account locked / too many attempts",
    515: "OK (operation succeeded)",
    607: "command not supported for this account",
}

_HEADER = struct.Struct("BB2xIIBBHI")  # 20 bytes
_SEND_HEADER = struct.Struct("BB2xII2xHI")  # 20 bytes, total/cur forced to 0


def ret_meaning(ret: Optional[int]) -> str:
    """Human-readable meaning of a DVRIP ``Ret`` code (handles None/unknown)."""
    if ret is None:
        return "no response"
    return RET_MEANING.get(ret, f"Ret={ret}")


def sofia_hash(password: str) -> str:
    """Return the 8-character "Sofia" hash of *password* used by DVRIP login.

    This is the well-documented XMeye/NETsurveillance scheme: take the MD5
    digest, fold byte pairs mod 62 and map each to ``[0-9A-Za-z]``.
    """
    digest = hashlib.md5(password.encode("utf-8")).digest()
    out = []
    for i in range(8):
        n = (digest[2 * i] + digest[2 * i + 1]) % 62
        if n > 9:
            n += 61 if n > 35 else 55
        else:
            n += 48
        out.append(chr(n))
    return "".join(out)


class DVRIPClient:
    """A single DVRIP session to one camera.

    Typical use::

        with DVRIPClient("192.0.2.10", user="rryp", password="secret") as cam:
            info = cam.get_info("SystemInfo")

    The client is intentionally low-level: higher-level features (file listing,
    playback, PTZ, ...) live in sibling modules that drive this class.
    """

    def __init__(
        self,
        host: str,
        user: str = "admin",
        password: str = "",
        port: int = DVRIP_PORT,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.timeout = timeout

        self.socket: Optional[socket.socket] = None
        self.session: int = 0
        self.packet_count: int = 0
        self.alive_time: int = 20
        self.last_login_ret: Optional[int] = None

    # -- connection management ------------------------------------------------
    def connect(self) -> None:
        """Open the TCP socket. Safe to call again after :meth:`close`."""
        try:
            self.socket = socket.create_connection((self.host, self.port), self.timeout)
            self.socket.settimeout(self.timeout)
        except OSError as exc:
            raise XMConnectionError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        """Close the socket and reset session state."""
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None
        self.session = 0
        self.packet_count = 0

    def _reconnect(self) -> None:
        self.close()
        self.connect()

    @property
    def session_hex(self) -> str:
        """The current session id formatted as DVRIP expects it in JSON bodies."""
        return "0x%08X" % self.session

    # -- framing --------------------------------------------------------------
    def _send_all(self, data: bytes) -> None:
        assert self.socket is not None
        try:
            self.socket.sendall(data)
        except OSError as exc:
            raise XMConnectionError(f"send failed: {exc}") from exc

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Read exactly *n* bytes. Return ``None`` on EOF/timeout before any byte."""
        assert self.socket is not None
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.socket.recv(n - len(buf))
            except socket.timeout:
                return bytes(buf) if buf else None
            if not chunk:
                return bytes(buf) if buf else None
            buf.extend(chunk)
        return bytes(buf)

    def send(self, msgid: int, data: Any, wait_response: bool = True) -> Optional[dict]:
        """Send one command and (optionally) return the parsed JSON reply.

        *data* may be a dict/list (JSON-encoded), ``str`` or ``bytes``. When
        *wait_response* is false the method returns ``None`` immediately after
        writing — used to kick off a binary media stream that is then read with
        :meth:`read_frame`.
        """
        if self.socket is None:
            self.connect()

        if isinstance(data, (dict, list)):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = bytes(data)

        packet = (
            _SEND_HEADER.pack(0xFF, 0, self.session, self.packet_count, msgid, len(payload) + 2) + payload + b"\x0a\x00"
        )
        self.packet_count += 1
        self._send_all(packet)
        if not wait_response:
            return None
        return self._recv_json()

    def _recv_json(self) -> dict:
        """Read one reply, reassembling multi-packet JSON, and parse it."""
        buf = bytearray()
        while True:
            header = self._recv_exact(20)
            if not header or len(header) < 20:
                raise ProtocolError("connection closed while reading reply")
            _h, _v, session, _seq, total, cur, _msgid, length = _HEADER.unpack(header)
            if session:
                self.session = session
            chunk = self._recv_exact(length) if length else b""
            if chunk:
                buf.extend(chunk)
            if total == 0 or cur >= total - 1:
                break

        if not buf:
            return {}
        text = bytes(buf).rstrip(b"\x00").rstrip(b"\x0a").rstrip(b"\x00")
        try:
            return json.loads(text.decode("utf-8", "ignore"))
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON reply: {exc}") from exc

    def read_frame(self) -> Tuple[Optional[int], Optional[bytes]]:
        """Read one raw frame. Return ``(msgid, payload)`` or ``(None, None)`` at EOF.

        Used by the playback/download layer to pull binary media frames off the
        same socket after a stream has been started.
        """
        header = self._recv_exact(20)
        if not header or len(header) < 20:
            return None, None
        _h, _v, _s, _seq, _t, _c, msgid, length = _HEADER.unpack(header)
        payload = self._recv_exact(length) if length else b""
        if length and payload is None:
            return None, None
        return msgid, payload

    # -- authentication -------------------------------------------------------
    def login(self) -> bool:
        """Authenticate, trying the Sofia hash first then plaintext fallback.

        Returns ``True`` on success. On failure returns ``False`` and records the
        camera's ``Ret`` code in :attr:`last_login_ret`. Raises
        :class:`XMConnectionError` if the socket cannot be (re)established.
        """
        if self.socket is None:
            self.connect()

        candidates = [sofia_hash(self.password)]
        if self.password and self.password not in candidates:
            candidates.append(self.password)  # plaintext fallback (newer XM firmware)

        last: Optional[dict] = None
        for index, pw in enumerate(candidates):
            resp = self.send(
                1000,
                {
                    "EncryptType": "MD5",
                    "LoginType": "DVRIP-Web",
                    "PassWord": pw,
                    "UserName": self.user,
                },
            )
            last = resp
            if resp and resp.get("Ret") in OK_CODES:
                self.session = int(resp["SessionID"], 16)
                self.alive_time = resp.get("AliveInterval", 20)
                return True
            # Account locked: do not keep hammering.
            if resp and resp.get("Ret") == 205:
                break
            # Camera usually drops the socket after a rejected attempt.
            if index < len(candidates) - 1:
                self._reconnect()

        self.last_login_ret = last.get("Ret") if last else None
        return False

    def login_or_raise(self) -> None:
        """Like :meth:`login` but raise :class:`LoginError` on failure."""
        if not self.login():
            ret = self.last_login_ret
            raise LoginError(f"DVRIP login failed for {self.host}: {ret_meaning(ret)}", ret=ret)

    def relogin(self) -> bool:
        """Drop the socket and log in again on a fresh connection.

        Xiongmai cameras keep a playback channel reserved after a download, so
        the next ``OPPlayBack`` ``Claim`` on the same connection fails with
        ``Ret 103``. Taking a fresh connection per file avoids that.
        """
        self.close()
        return self.login()

    def keep_alive(self) -> Optional[dict]:
        """Send a single KeepAlive. Call periodically on an otherwise idle session."""
        return self.send(1006, {"Name": "KeepAlive", "SessionID": self.session_hex})

    # -- convenience ----------------------------------------------------------
    def get_info(self, name: str) -> Any:
        """Fetch a named info/config block (``SystemInfo``, ``General``, ...).

        Returns the inner block when the camera nests it under *name*, otherwise
        the full reply dict.
        """
        resp = self.send(1042, {"Name": name, "SessionID": self.session_hex})
        if isinstance(resp, dict) and name in resp:
            return resp[name]
        return resp

    # -- context manager ------------------------------------------------------
    def __enter__(self) -> "DVRIPClient":
        self.login_or_raise()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
