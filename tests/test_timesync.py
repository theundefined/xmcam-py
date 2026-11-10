"""Offline tests for camera clock read/set (DVRIP 1450/1453). No real camera is contacted."""
from __future__ import annotations

import json
import struct
from datetime import datetime

import pytest

from xmcam.dvrip import DVRIPClient
from xmcam.exceptions import XMCamError
from xmcam.timesync import get_time, set_time, sync_to_host
from conftest import json_frame

_HEADER = struct.Struct("BB2xIIBBHI")


def _parse_sent(sent: bytes):
    """Split one sent packet into (msgid, parsed-json-body)."""
    _h, _v, _s, _seq, _t, _c, msgid, length = _HEADER.unpack(sent[:20])
    body = sent[20:20 + length].rstrip(b"\x00").rstrip(b"\x0a").rstrip(b"\x00")
    return msgid, json.loads(body.decode())


def _new_cam() -> DVRIPClient:
    cam = DVRIPClient("192.0.2.10", user="admin", password="x")
    cam.session = 0x1234
    return cam


class TestGetTime:
    def test_parses_optimequery(self, fake_socket_factory):
        sock = fake_socket_factory(
            json_frame(1453, {"Ret": 100, "Name": "OPTimeQuery", "OPTimeQuery": "2026-11-10 12:00:00"})
        )
        assert get_time(_new_cam()) == datetime(2026, 11, 10, 12, 0, 0)

        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1453
        assert body["Name"] == "OPTimeQuery"
        assert body["SessionID"] == "0x00001234"

    def test_returns_none_when_field_missing(self, fake_socket_factory):
        fake_socket_factory(json_frame(1453, {"Ret": 607}))
        assert get_time(_new_cam()) is None

    def test_returns_none_on_unparseable_value(self, fake_socket_factory):
        fake_socket_factory(json_frame(1453, {"Ret": 100, "OPTimeQuery": "not a date"}))
        assert get_time(_new_cam()) is None


class TestSetTime:
    def test_sends_formatted_time(self, fake_socket_factory):
        sock = fake_socket_factory(json_frame(1450, {"Ret": 100}))
        reply = set_time(_new_cam(), datetime(2026, 1, 2, 3, 4, 5))

        assert reply["Ret"] == 100
        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1450
        assert body["Name"] == "OPTimeSetting"
        assert body["OPTimeSetting"] == "2026-01-02 03:04:05"
        assert body["SessionID"] == "0x00001234"

    def test_accepts_ok_code_515(self, fake_socket_factory):
        fake_socket_factory(json_frame(1450, {"Ret": 515}))
        assert set_time(_new_cam(), datetime(2026, 1, 1, 0, 0, 0))["Ret"] == 515

    def test_raises_on_failure_ret(self, fake_socket_factory):
        fake_socket_factory(json_frame(1450, {"Ret": 102}))
        with pytest.raises(XMCamError):
            set_time(_new_cam(), datetime(2026, 1, 1, 0, 0, 0))

    def test_default_when_is_now(self, fake_socket_factory, monkeypatch):
        import xmcam.timesync as ts

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 10, 9, 8, 7)

        monkeypatch.setattr(ts, "datetime", _FixedDatetime)
        sock = fake_socket_factory(json_frame(1450, {"Ret": 100}))
        set_time(_new_cam())
        _msgid, body = _parse_sent(bytes(sock.sent))
        assert body["OPTimeSetting"] == "2026-11-10 09:08:07"


class TestSyncToHost:
    def test_sets_time_to_host_now(self, fake_socket_factory, monkeypatch):
        import xmcam.timesync as ts

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 10, 15, 30, 0)

        monkeypatch.setattr(ts, "datetime", _FixedDatetime)
        sock = fake_socket_factory(json_frame(1450, {"Ret": 100}))
        sync_to_host(_new_cam())
        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1450
        assert body["OPTimeSetting"] == "2026-11-10 15:30:00"
