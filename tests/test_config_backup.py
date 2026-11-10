"""Offline tests for config export/import (DVRIP 1040/1042). No real camera is contacted."""
from __future__ import annotations

import json
import struct

from xmcam.config_backup import (
    DEFAULT_CONFIG_BLOCKS,
    export_config,
    import_config,
    save_config,
)
from xmcam.dvrip import DVRIPClient
from conftest import json_frame

_HEADER = struct.Struct("BB2xIIBBHI")


def _parse_all_sent(sent: bytes):
    """Split the concatenated outgoing bytes into a list of (msgid, body)."""
    out = []
    pos = 0
    while pos < len(sent):
        _h, _v, _s, _seq, _t, _c, msgid, length = _HEADER.unpack(sent[pos:pos + 20])
        raw = sent[pos + 20:pos + 20 + length]
        body = raw.rstrip(b"\x00").rstrip(b"\x0a").rstrip(b"\x00")
        out.append((msgid, json.loads(body.decode())))
        pos += 20 + length
    return out


def _new_cam() -> DVRIPClient:
    cam = DVRIPClient("192.0.2.10", user="admin", password="x")
    cam.session = 0x1234
    return cam


class TestExportConfig:
    def test_default_blocks_requested_with_1042(self, fake_socket_factory):
        replies = b"".join(
            json_frame(1042, {"Ret": 100, name: {"v": i}})
            for i, name in enumerate(DEFAULT_CONFIG_BLOCKS)
        )
        sock = fake_socket_factory(replies)
        result = export_config(_new_cam())

        sent = _parse_all_sent(bytes(sock.sent))
        assert [m for m, _ in sent] == [1042] * len(DEFAULT_CONFIG_BLOCKS)
        assert [b["Name"] for _, b in sent] == DEFAULT_CONFIG_BLOCKS
        assert all(b["SessionID"] == "0x00001234" for _, b in sent)
        assert set(result) == set(DEFAULT_CONFIG_BLOCKS)
        assert result["Camera"] == {"v": 1}

    def test_block_with_607_is_skipped(self, fake_socket_factory):
        blocks = ["General", "Camera", "Encode"]
        replies = (
            json_frame(1042, {"Ret": 100, "General": {"a": 1}})
            + json_frame(1042, {"Ret": 607})
            + json_frame(1042, {"Ret": 100, "Encode": {"b": 2}})
        )
        fake_socket_factory(replies)
        result = export_config(_new_cam(), blocks=blocks)
        assert result == {"General": {"a": 1}, "Encode": {"b": 2}}
        assert "Camera" not in result

    def test_custom_blocks_only_requests_those(self, fake_socket_factory):
        sock = fake_socket_factory(json_frame(1042, {"Ret": 100, "General": {}}))
        export_config(_new_cam(), blocks=["General"])
        sent = _parse_all_sent(bytes(sock.sent))
        assert len(sent) == 1
        assert sent[0][1]["Name"] == "General"


class TestSaveConfig:
    def test_writes_json_file_and_returns_dict(self, fake_socket_factory, tmp_path):
        fake_socket_factory(json_frame(1042, {"Ret": 100, "General": {"x": 1}}))
        target = tmp_path / "backup.json"
        data = save_config(_new_cam(), target, blocks=["General"])

        assert data == {"General": {"x": 1}}
        assert json.loads(target.read_text(encoding="utf-8")) == {"General": {"x": 1}}


class TestImportConfig:
    def test_sends_1040_set_per_block_and_returns_rets(self, fake_socket_factory):
        replies = json_frame(1040, {"Ret": 100}) + json_frame(1040, {"Ret": 515})
        sock = fake_socket_factory(replies)
        results = import_config(
            _new_cam(),
            {"General": {"MachineName": "test"}, "Encode": {"Fps": 10}},
        )

        assert results == {"General": 100, "Encode": 515}
        sent = _parse_all_sent(bytes(sock.sent))
        assert [m for m, _ in sent] == [1040, 1040]
        g = sent[0][1]
        assert g["Name"] == "General"
        assert g["General"] == {"MachineName": "test"}
        assert g["SessionID"] == "0x00001234"
        assert sent[1][1]["Encode"] == {"Fps": 10}
