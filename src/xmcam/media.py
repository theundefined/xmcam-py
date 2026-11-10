"""Demuxing and remuxing of Xiongmai (XM) recording containers.

A recording downloaded over DVRIP is *not* a clean H.26x stream; it is
Xiongmai's own container made of frames that start with the 4-byte marker
``00 00 01 Fx``:

* ``0xFC`` / ``0xFE`` -- video I-frame, 16-byte header
  (``<BBBBII``: media, fps, width/8, height/8, datetime, length).
* ``0xFD`` -- video P-frame, 8-byte header (length at bytes 4..8).
* ``0xFA`` -- audio, 8-byte header (``<BBH``: media, sample-rate code, length).
* ``0xF9`` -- info, 8-byte header (``<BBH``); skipped.

The video payloads concatenate into a valid elementary stream, which
:func:`remux_to_mp4` can wrap into an MP4 with ``ffmpeg -c copy``.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

__all__ = ["demux", "codec_from_es", "have_ffmpeg", "remux_to_mp4", "convert_raw"]

_CODECS = {1: "mpeg4", 2: "h264", 3: "h265"}


def codec_from_es(es: bytes, scan_limit: int = 2_000_000) -> str | None:
    """Detect the codec of an H.26x elementary stream by inspecting NAL units.

    This is firmware-independent: it reads the NAL unit types after the Annex-B
    start codes instead of trusting the Xiongmai ``media`` byte, whose encoding
    varies between firmwares (e.g. 83 for HEVC on XM530 / ONVIF 21.06). HEVC
    VPS/SPS/PPS/IRAP and H.264 IDR/SPS/PPS signatures disambiguate cleanly.
    """
    i, n = 0, min(len(es), scan_limit)
    while i + 4 < n:
        if es[i : i + 3] == b"\x00\x00\x01":
            b = es[i + 3]
            if b & 0x80:  # forbidden_zero_bit set -> not a valid NAL header
                i += 3
                continue
            t265 = (b >> 1) & 0x3F
            t264 = b & 0x1F
            if 16 <= t265 <= 34:  # HEVC: IRAP slices (16-21) + VPS/SPS/PPS (32-34)
                return "h265"
            if t264 in (5, 7, 8):  # H.264: IDR slice / SPS / PPS
                return "h264"
            i += 3
        else:
            i += 1
    return None


def demux(raw: bytes) -> tuple[bytes, bytes, str | None]:
    """Split an XM container into ``(video_es, audio_bytes, codec_name)``.

    Unknown bytes between frames are skipped one byte at a time, so trailing
    or leading garbage is tolerated. ``codec_name`` is ``"h264"``, ``"h265"``,
    ``"mpeg4"`` or ``None`` when no I-frame header announced a known codec.
    """
    video = bytearray()
    audio = bytearray()
    codec: str | None = None
    i, n = 0, len(raw)
    while i + 8 <= n:
        if raw[i : i + 3] != b"\x00\x00\x01":
            i += 1
            continue
        ft = raw[i + 3]
        if ft in (0xFC, 0xFE):
            if i + 16 > n:
                break
            media, _fps, _w, _h, _dt, length = struct.unpack("<BBBBII", raw[i + 4 : i + 16])
            codec = _CODECS.get(media, codec)
            start = i + 16
            video += raw[start : start + length]
            i = start + length
        elif ft == 0xFD:
            length = struct.unpack("<I", raw[i + 4 : i + 8])[0]
            start = i + 8
            video += raw[start : start + length]
            i = start + length
        elif ft == 0xFA:
            _media, _samp, length = struct.unpack("<BBH", raw[i + 4 : i + 8])
            start = i + 8
            audio += raw[start : start + length]
            i = start + length
        elif ft == 0xF9:
            _media, _n, length = struct.unpack("<BBH", raw[i + 4 : i + 8])
            i = i + 8 + length
        else:
            i += 1
    if codec is None:
        # The media byte did not map to a known codec (firmware-specific
        # value); fall back to inspecting the elementary stream itself.
        codec = codec_from_es(bytes(video))
    return bytes(video), bytes(audio), codec


def have_ffmpeg() -> bool:
    """Return True if an ``ffmpeg`` executable is found on ``PATH``."""
    return shutil.which("ffmpeg") is not None


def remux_to_mp4(video_es: bytes, codec: str | None, out_path, framerate: int = 15) -> bool:
    """Wrap a raw H.26x elementary stream into an MP4 without re-encoding.

    The stream is written to a temporary file next to ``out_path`` (``.h264``
    for H.264, ``.h265`` otherwise) and removed afterwards. Returns True when
    ffmpeg exits with 0 and produced a non-empty output file.
    """
    out = Path(out_path)
    ext = ".h264" if codec == "h264" else ".h265"
    es_path = out.with_name(out.name + ".es" + ext)
    es_path.write_bytes(video_es)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-framerate",
        str(framerate),
        "-i",
        str(es_path),
        "-c",
        "copy",
    ]
    # Tag HEVC as hvc1 so the MP4 opens in the widest set of players
    # (QuickTime/Apple in particular; harmless for mplayer/VLC/mpv).
    if (codec or "").lower() in ("h265", "hevc"):
        cmd += ["-tag:v", "hvc1"]
    cmd.append(str(out))
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True)
        except OSError:
            return False
        return proc.returncode == 0 and out.exists() and out.stat().st_size > 0
    finally:
        try:
            os.remove(es_path)
        except OSError:
            pass


def convert_raw(raw_path, mp4_path, keep_raw: bool = False, framerate: int = 15) -> bool:
    """Convert a downloaded XM ``.raw`` recording to MP4.

    Returns False if the file holds no video or ffmpeg fails (the raw file is
    kept in that case). On success the raw file is deleted unless ``keep_raw``.
    """
    raw_p = Path(raw_path)
    video, _audio, codec = demux(raw_p.read_bytes())
    if not video:
        return False
    if not remux_to_mp4(video, codec, mp4_path, framerate=framerate):
        return False
    if not keep_raw:
        raw_p.unlink()
    return True
