"""Shared test helpers.

These let the whole suite exercise the DVRIP layer without a real camera by
feeding pre-built frames through a fake socket. Keeping them in ``conftest.py``
makes them importable from every test module.
"""
from __future__ import annotations

import json
import socket
import struct
from typing import List

import pytest

_HEADER = struct.Struct("BB2xIIBBHI")


def build_frame(msgid: int, payload: bytes, session: int = 0, total: int = 0, cur: int = 0) -> bytes:
    """Build one raw DVRIP frame (20-byte header + payload)."""
    return _HEADER.pack(0xFF, 0, session, 0, total, cur, msgid, len(payload)) + payload


def json_frame(msgid: int, obj: dict, session: int = 0, total: int = 0, cur: int = 0) -> bytes:
    """Build a DVRIP frame carrying a JSON object (terminated with 0x0a 0x00)."""
    payload = json.dumps(obj).encode("utf-8") + b"\x0a\x00"
    return build_frame(msgid, payload, session=session, total=total, cur=cur)


class FakeSocket:
    """A minimal stand-in for a connected TCP socket.

    It replays a scripted byte stream to ``recv`` and records everything written
    with ``sendall``, so tests can assert on what the client sent and simulate
    what the camera replied.
    """

    def __init__(self, inbound: bytes = b"") -> None:
        self.inbound = bytearray(inbound)
        self.sent = bytearray()
        self.closed = False
        self._timeout = None

    def feed(self, data: bytes) -> None:
        self.inbound.extend(data)

    def recv(self, n: int) -> bytes:
        if not self.inbound:
            return b""  # EOF
        chunk = bytes(self.inbound[:n])
        del self.inbound[:n]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def settimeout(self, value) -> None:
        self._timeout = value

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_socket_factory(monkeypatch):
    """Patch ``socket.create_connection`` to hand back a prepared :class:`FakeSocket`.

    Usage::

        sock = fake_socket_factory(login_reply_bytes)
        client = DVRIPClient("192.0.2.10", password="x")
        client.login()
        assert sock.sent  # the client wrote a login packet
    """
    created: List[FakeSocket] = []

    def _factory(inbound: bytes = b"") -> FakeSocket:
        sock = FakeSocket(inbound)
        created.append(sock)

        def _fake_create_connection(address, timeout=None, *args, **kwargs):
            return sock

        monkeypatch.setattr(socket, "create_connection", _fake_create_connection)
        return sock

    return _factory
