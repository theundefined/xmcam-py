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
    assert cmd[cmd.index("-c:v") + 1] == "copy"  # the test blob carries an audio track
    assert cmd[cmd.index("-c:a") + 1] == "aac"
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


def _xm_stream(seconds=12, fps=12, rate_code=2):
    """A synthetic XM recording: ``fps`` video frames and 8 kHz A-law audio per second."""
    out = bytearray()
    frames = seconds * fps
    audio_frames = seconds * 25  # 40 ms of 8 kHz audio each (320 bytes)
    video_done = audio_done = 0
    for _ in range(frames + audio_frames):
        # interleave by time, like the camera does
        if video_done / fps <= audio_done / 25 and video_done < frames:
            if video_done % 24 == 0:
                payload = b"\x00\x00\x00\x01\x40" + bytes([video_done % 251]) * 20
                out += b"\x00\x00\x01\xfc" + struct.pack("<BBBBII", 83, fps, 40, 34, 0, len(payload)) + payload
            else:
                payload = b"\x00\x00\x00\x01\x02" + bytes([video_done % 251]) * 30
                out += b"\x00\x00\x01\xfd" + struct.pack("<I", len(payload)) + payload
            video_done += 1
        elif audio_done < audio_frames:
            out += b"\x00\x00\x01\xfa" + struct.pack("<BBH", 14, rate_code, 320) + bytes([audio_done % 251]) * 320
            audio_done += 1
    return bytes(out)


def test_incremental_demux_matches_one_shot_for_any_chunking():
    import random

    blob = b"\x07" + _xm_stream(seconds=8) + b"\xff\xee"
    video, audio, _codec = media.demux(blob)
    rnd = random.Random(1234)
    for _ in range(25):
        dem = media.XmDemuxer()
        got_v, got_a = bytearray(), bytearray()
        i = 0
        while i < len(blob):
            step = rnd.choice([1, 2, 7, 19, 100, 333, 4096])
            v, a = dem.feed(blob[i : i + step])
            got_v += v
            got_a += a
            i += step
        v, a = dem.finish()
        got_v += v
        got_a += a
        assert bytes(got_v) == video and bytes(got_a) == audio


def test_incremental_demux_truncated_tail_matches_one_shot():
    blob = _xm_stream(seconds=3)[:-100]  # cut in the middle of the last frame
    video, audio, _ = media.demux(blob)
    dem = media.XmDemuxer()
    v1, a1 = dem.feed(blob)
    v2, a2 = dem.finish()
    assert v1 + v2 == video and a1 + a2 == audio


def test_demuxer_reports_stream_facts():
    dem = media.XmDemuxer()
    dem.feed(_xm_stream(seconds=2))
    assert dem.fps == 12
    assert (dem.width, dem.height) == (320, 272)
    assert dem.audio_codec == "alaw" and dem.audio_rate == 8000
    assert dem.measured_fps is None  # 2 s of audio is too little to trust


def test_measured_fps_uses_the_audio_clock():
    # 12.1 real frames per second against a nominal 12: measured from audio, not the header.
    dem = media.XmDemuxer()
    out = bytearray()
    frames = 0
    for sec in range(20):
        for k in range(25):
            out += b"\x00\x00\x01\xfa" + struct.pack("<BBH", 14, 2, 320) + b"\x55" * 320
        n = 12 + (1 if sec % 10 == 0 else 0)  # 242 frames in 20 s
        for _ in range(n):
            payload = b"\x00\x00\x00\x01\x02" + b"x" * 10
            out += b"\x00\x00\x01\xfd" + struct.pack("<I", len(payload)) + payload
            frames += 1
    out = b"\x00\x00\x01\xfc" + struct.pack("<BBBBII", 83, 12, 40, 34, 0, 5) + b"\x00\x00\x00\x01\x40" + bytes(out)
    dem.feed(out)
    assert dem.measured_fps is not None
    assert abs(dem.measured_fps - (frames + 1) / 20.0) < 1e-6


def test_convert_raw_adds_audio_and_uses_measured_fps(tmp_path, monkeypatch):
    calls = []
    fake_ffmpeg(monkeypatch, 0, calls)
    raw, mp4 = tmp_path / "e.raw", tmp_path / "e.mp4"
    raw.write_bytes(_xm_stream(seconds=12, fps=12))
    assert media.convert_raw(raw, mp4, keep_raw=True) is True
    cmd = calls[0]
    assert cmd.count("-i") == 2
    assert cmd[cmd.index("-ar") + 1] == "8000" and cmd[cmd.index("-f", cmd.index("-i") + 2) + 1] == "alaw"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-framerate") + 1] == "12"  # nominal rate, only for probing the stream
    assert cmd[cmd.index("-bsf:v") + 1].startswith("setts=ts=N/(12")  # real rate from the audio clock
    assert cmd[cmd.index("-aac_coder") + 1] == "fast"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["e.mp4", "e.raw"]  # temp ES + audio removed


def test_convert_raw_retries_video_only_when_audio_mux_fails(tmp_path, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        rc = 1 if "-c:a" in cmd else 0
        if rc == 0:
            with open(cmd[-1], "wb") as f:
                f.write(b"MP4DATA")
        return subprocess.CompletedProcess(cmd, rc, b"", b"")

    monkeypatch.setattr(media.subprocess, "run", run)
    raw, mp4 = tmp_path / "f.raw", tmp_path / "f.mp4"
    raw.write_bytes(_xm_stream(seconds=12))
    assert media.convert_raw(raw, mp4, keep_raw=True) is True
    assert len(calls) == 2 and "-c:a" not in calls[1]


def test_events_keep_frame_structure_and_audio_clock():
    blob = _xm_stream(seconds=3, fps=12)
    dem = media.XmDemuxer()
    events = []
    i = 0
    while i < len(blob):  # arbitrary chunking
        events += dem.feed_events(blob[i : i + 777])
        i += 777
    events += dem.finish_events()
    video = [e for e in events if e[0] == "V"]
    audio = [e for e in events if e[0] == "A"]
    assert len(video) == 3 * 12 and len(audio) == 3 * 25
    assert [e[2] for e in video][:25] == [True] + [False] * 23 + [True]  # I-frame every 24 in the synthetic stream
    # concatenation equals the one-shot demux
    one_v, one_a, _ = media.demux(blob)
    assert b"".join(e[1] for e in video) == one_v and b"".join(e[1] for e in audio) == one_a
    # the audio clock: bytes of audio before a frame grow with time (8 kHz, 1 byte/sample)
    before = [e[3] for e in video]
    assert before == sorted(before)
    assert abs(before[-1] / 8000.0 - 3 * (35 / 36)) < 0.1
