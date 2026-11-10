"""Tests for the low-level DVRIP client, using the fake socket from conftest."""

from __future__ import annotations

import json
import re
import struct

from xmcam.dvrip import DVRIPClient, sofia_hash
from conftest import build_frame, json_frame

_HEADER = struct.Struct("BB2xIIBBHI")


def _parse_sent(sent: bytes):
    """Split one sent packet into (msgid, parsed-json-body)."""
    header = sent[:20]
    _h, _v, _s, _seq, _t, _c, msgid, length = _HEADER.unpack(header)
    body = sent[20 : 20 + length].rstrip(b"\x00").rstrip(b"\x0a").rstrip(b"\x00")
    return msgid, json.loads(body.decode())


class TestSofiaHash:
    def test_length_and_charset(self):
        h = sofia_hash("whatever")
        assert len(h) == 8
        assert re.fullmatch(r"[0-9A-Za-z]{8}", h)

    def test_deterministic(self):
        assert sofia_hash("admin") == sofia_hash("admin")

    def test_known_vectors(self):
        # Regression vectors for the canonical XMeye/NETsurveillance scheme.
        assert sofia_hash("") == "tlJwpbo6"
        assert sofia_hash("admin") == "6QNMIQGe"


class TestLogin:
    def test_login_success_sets_session(self, fake_socket_factory):
        reply = json_frame(1001, {"Ret": 100, "SessionID": "0x00000005", "AliveInterval": 20})
        sock = fake_socket_factory(reply)

        cam = DVRIPClient("192.0.2.10", user="rryp", password="secret")
        assert cam.login() is True
        assert cam.session == 5

        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1000
        assert body["UserName"] == "rryp"
        # First attempt uses the Sofia hash, not the plaintext password.
        assert body["PassWord"] == sofia_hash("secret")

    def test_login_failure_records_ret(self, fake_socket_factory):
        # Both attempts (hash, then plaintext) get rejected with Ret 203.
        reply = json_frame(1001, {"Ret": 203}) * 2
        fake_socket_factory(reply)

        cam = DVRIPClient("192.0.2.10", password="bad")
        assert cam.login() is False
        assert cam.last_login_ret == 203


class TestFraming:
    def test_multipacket_json_reassembly(self, fake_socket_factory):
        full = json.dumps({"Ret": 100, "Name": "X", "value": "y" * 50}).encode() + b"\x0a\x00"
        half = len(full) // 2
        inbound = build_frame(1001, full[:half], total=2, cur=0) + build_frame(1001, full[half:], total=2, cur=1)
        fake_socket_factory(inbound)

        cam = DVRIPClient("192.0.2.10")
        cam.connect()
        reply = cam._recv_json()
        assert reply["Ret"] == 100
        assert reply["value"] == "y" * 50

    def test_read_frame_then_eof(self, fake_socket_factory):
        inbound = build_frame(1426, b"\x00\x00\x01\xfc payload")
        fake_socket_factory(inbound)

        cam = DVRIPClient("192.0.2.10")
        cam.connect()
        msgid, payload = cam.read_frame()
        assert msgid == 1426
        assert payload.endswith(b"payload")
        # Socket is now drained -> EOF.
        assert cam.read_frame() == (None, None)
