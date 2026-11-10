"""Offline tests for xmcam.media (ffmpeg is simulated)."""

from __future__ import annotations

import struct
import subprocess

from xmcam import media

I_VIDEO = b"\x00\x00\x00\x01\x40IFRAMEDATA"
P_VIDEO = b"\x00\x00\x00\x01\x02PFRAMEDATA!"
AUDIO = b"\xaa\xbb\xcc\xdd"


def build_blob(media_code: int = 3) -> bytes:
    blob = b"\x07"  # leading garbage to prove resync
    blob += b"\x00\x00\x01\xfc" + struct.pack("<BBBBII", media_code, 15, 80, 45, 0x12345678, len(I_VIDEO)) + I_VIDEO
    blob += b"\x00\x00\x01\xf9" + struct.pack("<BBH", 1, 0, 6) + b"SKIPME"
    blob += b"\x00\x00\x01\xfa" + struct.pack("<BBH", 14, 5, len(AUDIO)) + AUDIO
    blob += b"\x00\x00\x01\xfd" + struct.pack("<I", len(P_VIDEO)) + P_VIDEO
    blob += b"\xff\xee"  # trailing garbage
    return blob


def test_demux_basic():
    video, audio, codec = media.demux(build_blob())
    assert video == I_VIDEO + P_VIDEO
    assert audio == AUDIO
    assert codec == "h265"


def test_demux_codecs_and_empty():
    assert media.demux(build_blob(2))[2] == "h264"
    assert media.demux(build_blob(1))[2] == "mpeg4"
    assert media.demux(b"garbage") == (b"", b"", None)


def test_demux_fe_frame():
    blob = b"\x00\x00\x01\xfe" + struct.pack("<BBBBII", 2, 10, 1, 1, 0, 3) + b"abc"
    assert media.demux(blob) == (b"abc", b"", "h264")


def test_have_ffmpeg(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert media.have_ffmpeg() is True
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.have_ffmpeg() is False


def fake_ffmpeg(monkeypatch, rc=0, record=None):
    def run(cmd, **kwargs):
        if record is not None:
            record.append(cmd)
        if rc == 0:
            with open(cmd[-1], "wb") as f:
                f.write(b"MP4DATA")
        return subprocess.CompletedProcess(cmd, rc, b"", b"")

    monkeypatch.setattr(media.subprocess, "run", run)


def test_convert_raw_success_deletes_raw(tmp_path, monkeypatch):
    calls = []
    fake_ffmpeg(monkeypatch, 0, calls)
    raw, mp4 = tmp_path / "a.raw", tmp_path / "a.mp4"
    raw.write_bytes(build_blob(3))
    assert media.convert_raw(raw, mp4, framerate=20) is True
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-framerate") + 1] == "20"
    assert cmd[cmd.index("-i") + 1].endswith(".h265")
    assert mp4.read_bytes() == b"MP4DATA"
    assert not raw.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mp4"]  # temp ES removed


def test_convert_raw_h264_extension_and_keep_raw(tmp_path, monkeypatch):
    calls = []
    fake_ffmpeg(monkeypatch, 0, calls)
    raw, mp4 = tmp_path / "b.raw", tmp_path / "b.mp4"
    raw.write_bytes(build_blob(2))
    assert media.convert_raw(raw, mp4, keep_raw=True) is True
    cmd = calls[0]
    assert cmd[cmd.index("-i") + 1].endswith(".h264")
    assert cmd[cmd.index("-framerate") + 1] == "15"
    assert raw.exists()


def test_convert_raw_ffmpeg_failure_keeps_raw(tmp_path, monkeypatch):
    fake_ffmpeg(monkeypatch, 1)
    raw, mp4 = tmp_path / "c.raw", tmp_path / "c.mp4"
    raw.write_bytes(build_blob())
    assert media.convert_raw(raw, mp4) is False
    assert raw.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.raw"]


def test_convert_raw_no_video(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ffmpeg must not run")

    monkeypatch.setattr(media.subprocess, "run", boom)
    raw = tmp_path / "d.raw"
    raw.write_bytes(b"nothing here")
    assert media.convert_raw(raw, tmp_path / "d.mp4") is False
    assert raw.exists()


def test_remux_missing_ffmpeg_returns_false(tmp_path, monkeypatch):
    def run(*a, **k):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(media.subprocess, "run", run)
    assert media.remux_to_mp4(b"x", "h264", tmp_path / "e.mp4") is False
