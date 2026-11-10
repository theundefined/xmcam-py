"""Offline tests for PTZ control (DVRIP 1400). No real camera is contacted."""
from __future__ import annotations

import json
import struct

import pytest

from xmcam.dvrip import DVRIPClient
from xmcam.exceptions import XMCamError
from xmcam.ptz import PTZ_COMMANDS, ptz_move, ptz_stop
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


class TestPtzMove:
    @pytest.mark.parametrize(
        "direction,command",
        [
            ("up", "DirectionUp"),
            ("down", "DirectionDown"),
            ("left", "DirectionLeft"),
            ("right", "DirectionRight"),
            ("zoomin", "ZoomTile"),
            ("zoomout", "ZoomWide"),
        ],
    )
    def test_direction_maps_to_dvrip_command(self, fake_socket_factory, direction, command):
        assert PTZ_COMMANDS[direction] == command
        sock = fake_socket_factory(json_frame(1400, {"Ret": 100}))
        ptz_move(_new_cam(), direction)

        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1400
        assert body["Name"] == "OPPTZControl"
        assert body["OPPTZControl"]["Command"] == command

    def test_outgoing_body_has_expected_structure(self, fake_socket_factory):
        sock = fake_socket_factory(json_frame(1400, {"Ret": 100}))
        ptz_move(_new_cam(), "left", channel=2, step=7)

        _msgid, body = _parse_sent(bytes(sock.sent))
        param = body["OPPTZControl"]["Parameter"]
        assert param == {
            "AUX": {"Number": 0, "Status": "On"},
            "Channel": 2,
            "MenuOpt": "Enter",
            "POINT": {"bottom": 0, "left": 0, "right": 0, "top": 0},
            "Preset": -1,
            "Step": 7,
            "Type": "Direction",
        }

    def test_returns_reply_dict(self, fake_socket_factory):
        fake_socket_factory(json_frame(1400, {"Ret": 100, "Name": "OPPTZControl"}))
        reply = ptz_move(_new_cam(), "up")
        assert reply["Ret"] == 100
        assert reply["Name"] == "OPPTZControl"

    def test_unsupported_607_is_returned_not_raised(self, fake_socket_factory):
        fake_socket_factory(json_frame(1400, {"Ret": 607}))
        reply = ptz_move(_new_cam(), "zoomin")
        assert reply["Ret"] == 607

    def test_other_ret_raises(self, fake_socket_factory):
        fake_socket_factory(json_frame(1400, {"Ret": 203}))
        with pytest.raises(XMCamError):
            ptz_move(_new_cam(), "up")

    def test_unknown_direction_raises_value_error_without_sending(self, fake_socket_factory):
        sock = fake_socket_factory(b"")
        with pytest.raises(ValueError):
            ptz_move(_new_cam(), "diagonal")
        assert sock.sent == bytearray()

    def test_stop_forces_step_zero(self, fake_socket_factory):
        sock = fake_socket_factory(json_frame(1400, {"Ret": 100}))
        ptz_move(_new_cam(), "up", step=5, stop=True)
        _msgid, body = _parse_sent(bytes(sock.sent))
        assert body["OPPTZControl"]["Parameter"]["Step"] == 0


class TestPtzStop:
    def test_ptz_stop_sends_step_zero_same_command(self, fake_socket_factory):
        sock = fake_socket_factory(json_frame(1400, {"Ret": 100}))
        ptz_stop(_new_cam(), "down", channel=1)

        msgid, body = _parse_sent(bytes(sock.sent))
        assert msgid == 1400
        assert body["OPPTZControl"]["Command"] == "DirectionDown"
        assert body["OPPTZControl"]["Parameter"]["Step"] == 0
        assert body["OPPTZControl"]["Parameter"]["Channel"] == 1
