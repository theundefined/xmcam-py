"""Tests for the typed data models."""

from __future__ import annotations

from datetime import datetime

from xmcam.models import Recording, parse_event, parse_file_length


class TestParseFileLength:
    def test_hex_kilobytes_to_bytes(self):
        # 0xc3cc = 50124 KB -> 51 326 976 bytes (verified byte-exact on real files).
        assert parse_file_length("0x0000c3cc") == 50124 * 1024

    def test_plain_int_kilobytes(self):
        assert parse_file_length(100) == 100 * 1024

    def test_garbage_is_zero(self):
        assert parse_file_length("not-a-number") == 0
        assert parse_file_length(None) == 0


class TestRecording:
    def test_from_dict(self):
        entry = {
            "FileName": "/idea0/2026-11-10/001/08.00.00-08.05.00[R][@41ae][0].h264",
            "BeginTime": "2026-11-10 08:00:00",
            "EndTime": "2026-11-10 08:05:00",
            "FileLength": "0x0000c3cc",
            "DiskNo": 0,
            "SerialNo": 1,
        }
        rec = Recording.from_dict(entry, channel=0, stream_type="Main")
        assert rec.filename.endswith(".h264")
        assert rec.begin_time == datetime(2026, 11, 10, 8, 0, 0)
        assert rec.end_time == datetime(2026, 11, 10, 8, 5, 0)
        assert rec.length_bytes == 50124 * 1024
        assert rec.duration_seconds == 300.0
        assert rec.raw is entry


class TestEvent:
    def test_parse_event(self):
        assert parse_event("/x/08.10.24-08.10.41[M][@4ae7][1].h264") == "M"
        assert parse_event("/x/08.00.00-08.05.00[R][@1][0].h264") == "R"
        assert parse_event("/x/no-tag.h264") == ""

    def test_is_motion_and_name(self):
        rec = Recording.from_dict(
            {
                "FileName": "/idea0/2026-11-10/001/08.10.24-08.10.41[M][@4ae7][1].h264",
                "BeginTime": "2026-11-10 08:10:24",
                "EndTime": "2026-11-10 08:10:41",
                "FileLength": "0x1",
            }
        )
        assert rec.event == "M"
        assert rec.is_motion is True
        assert rec.event_name == "motion"
