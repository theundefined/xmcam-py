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
from typing import Iterator

__all__ = ["demux", "XmDemuxer", "codec_from_es", "have_ffmpeg", "remux_to_mp4", "convert_raw"]

_CODECS = {1: "mpeg4", 2: "h264", 3: "h265"}

#: Audio ``media`` byte -> codec name (G.711 A-law, as recorded by the tested XM530 cameras).
_AUDIO_CODECS = {14: "alaw"}
#: Audio sample-rate code -> Hz (code 2 = 8 kHz was verified: 40 ms per 320-byte frame).
_AUDIO_RATES = {1: 4000, 2: 8000, 3: 11025, 4: 16000, 5: 20000, 6: 22050, 7: 32000, 8: 44100, 9: 48000}
#: Longest plausible single frame; larger "lengths" are treated as a false start code.
_MAX_FRAME = 16 * 1024 * 1024
#: Audio shorter than this is too short to measure the real frame rate from.
_MIN_MEASURE_SECONDS = 5.0


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


class XmDemuxer:
    """Incremental XM demuxer: feed the container in arbitrary chunks.

    :meth:`feed` returns the video and audio payload bytes completed by that
    chunk; concatenated, they equal what :func:`demux` returns for the whole
    input. Stream facts (codec byte, frame rate, audio format) become available
    as soon as the first matching frame header has been seen.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.media_codec: str | None = None  # from the I-frame ``media`` byte (may stay None)
        self.fps: int | None = None  # nominal rate from the I-frame header
        self.width: int | None = None
        self.height: int | None = None
        self.audio_codec: str | None = None  # "alaw" or None when absent/unknown
        self.audio_rate: int | None = None
        self.video_frames = 0
        self.audio_bytes = 0

    def feed(self, data: bytes) -> tuple[bytes, bytes]:
        """Add *data*; return ``(video_bytes, audio_bytes)`` completed so far."""
        self._buf += data
        return self._parse(final=False)

    def finish(self) -> tuple[bytes, bytes]:
        """No more input: emit what remains (a truncated last frame is kept, like :func:`demux`)."""
        return self._parse(final=True)

    @property
    def measured_fps(self) -> float | None:
        """Real frame rate using the audio as a clock, or ``None`` if it cannot be trusted.

        The nominal header rate is only approximate (a measured 10-minute clip ran
        at 12.11 fps against a header value of 12), which makes audio drift. The
        measurement needs a few seconds of audio and must be close to the nominal rate.
        """
        if not (self.audio_codec == "alaw" and self.audio_rate and self.fps):
            return None
        seconds = self.audio_bytes / float(self.audio_rate)  # 1 byte per sample
        if seconds < _MIN_MEASURE_SECONDS or self.video_frames == 0:
            return None
        fps = self.video_frames / seconds
        return fps if 0.85 * self.fps <= fps <= 1.15 * self.fps else None

    def feed_events(self, data: bytes) -> list[tuple[str, bytes, bool, int]]:
        """Like :meth:`feed`, but keep the frame structure: a list of events.

        Each event is ``(kind, payload, is_key, audio_bytes_before)``: *kind* is
        ``"V"`` (one video frame, *is_key* for an I-frame) or ``"A"`` (audio). The
        last field counts the audio bytes that came before the frame, so audio is a
        capture clock for the video frames (camera frame rates are not constant).
        """
        self._buf += data
        return list(self._events(final=False))

    def finish_events(self) -> list[tuple[str, bytes, bool, int]]:
        """:meth:`finish` for the event API."""
        return list(self._events(final=True))

    def _parse(self, final: bool) -> tuple[bytes, bytes]:
        video = bytearray()
        audio = bytearray()
        for kind, payload, _key, _before in self._events(final):
            (video if kind == "V" else audio).extend(payload)
        return bytes(video), bytes(audio)

    def _events(self, final: bool) -> Iterator[tuple[str, bytes, bool, int]]:
        buf = self._buf
        n = len(buf)
        i = 0
        while i + 8 <= n:
            if buf[i : i + 3] != b"\x00\x00\x01":
                i += 1
                continue
            ft = buf[i + 3]
            if ft in (0xFC, 0xFE):
                if i + 16 > n:
                    break
                media, fps, w, h, _dt, length = struct.unpack("<BBBBII", buf[i + 4 : i + 16])
                end = i + 16 + length
                if length > _MAX_FRAME:
                    i += 1
                    continue
                if end > n and not final:
                    break
                self.media_codec = _CODECS.get(media, self.media_codec)
                if 1 <= fps <= 60:
                    self.fps = fps
                self.width, self.height = w * 8, h * 8
                self.video_frames += 1
                payload = bytes(buf[i + 16 : end])
                i = end
                yield "V", payload, True, self.audio_bytes
            elif ft == 0xFD:
                length = struct.unpack("<I", buf[i + 4 : i + 8])[0]
                end = i + 8 + length
                if length > _MAX_FRAME:
                    i += 1
                    continue
                if end > n and not final:
                    break
                self.video_frames += 1
                payload = bytes(buf[i + 8 : end])
                i = end
                yield "V", payload, False, self.audio_bytes
            elif ft == 0xFA:
                media, samp, length = struct.unpack("<BBH", buf[i + 4 : i + 8])
                end = i + 8 + length
                if end > n and not final:
                    break
                if self.audio_codec is None and media in _AUDIO_CODECS and samp in _AUDIO_RATES:
                    self.audio_codec = _AUDIO_CODECS[media]
                    self.audio_rate = _AUDIO_RATES[samp]
                payload = bytes(buf[i + 8 : end])
                before = self.audio_bytes
                self.audio_bytes += len(payload)
                i = end
                yield "A", payload, False, before
            elif ft == 0xF9:
                length = struct.unpack("<BBH", buf[i + 4 : i + 8])[2]
                end = i + 8 + length
                if end > n and not final:
                    break
                i = end
            else:
                i += 1
        del buf[: min(i, n)]


def have_ffmpeg() -> bool:
    """Return True if an ``ffmpeg`` executable is found on ``PATH``."""
    return shutil.which("ffmpeg") is not None


def _fmt_rate(fps: float) -> str:
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def remux_to_mp4(
    video_es: bytes,
    codec: str | None,
    out_path,
    framerate: float = 15,
    audio: tuple[bytes, str, int] | None = None,
    sync_fps: float | None = None,
) -> bool:
    """Wrap a raw H.26x elementary stream into an MP4 without re-encoding.

    The stream is written to a temporary file next to ``out_path`` (``.h264``
    for H.264, ``.h265`` otherwise) and removed afterwards. *audio* is an optional
    ``(bytes, "alaw", sample_rate)`` track, encoded to AAC; if muxing with audio
    fails the MP4 is retried video-only. *sync_fps* (the real frame rate measured
    from the audio clock) re-times the video frames to that rate: ffmpeg otherwise
    trusts the rate signalled in the HEVC stream, which is slightly off and makes
    the audio drift. Returns True when ffmpeg exits with 0 and produced a
    non-empty output file.
    """
    out = Path(out_path)
    ext = ".h264" if codec == "h264" else ".h265"
    es_path = out.with_name(out.name + ".es" + ext)
    audio_path = out.with_name(out.name + ".es.alaw")
    es_path.write_bytes(video_es)
    try:
        if audio is not None and audio[0]:
            audio_path.write_bytes(audio[0])
            if _run_remux(es_path, codec, out, framerate, audio_path, audio[2], sync_fps):
                return True
            if out.exists():
                out.unlink()
        return _run_remux(es_path, codec, out, framerate, None, 0, None)
    finally:
        for tmp in (es_path, audio_path):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _run_remux(
    es_path: Path, codec: str | None, out: Path, framerate: float, audio_path, audio_rate: int, sync_fps: float | None
) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-framerate",
        _fmt_rate(framerate),
        "-i",
        str(es_path),
    ]
    if audio_path is not None:
        cmd += ["-f", "alaw", "-ar", str(audio_rate), "-ac", "1", "-i", str(audio_path)]
    if audio_path is not None:
        # "fast" coder: the default one needs ~17 s for ten minutes of audio, this ~2 s.
        cmd += ["-c:v", "copy", "-c:a", "aac", "-aac_coder", "fast", "-b:a", "32k"]
    else:
        cmd += ["-c", "copy"]
    if sync_fps:
        cmd += ["-bsf:v", f"setts=ts=N/({_fmt_rate(sync_fps)}*TB)"]
    # Tag HEVC as hvc1 so the MP4 opens in the widest set of players
    # (QuickTime/Apple in particular; harmless for mplayer/VLC/mpv).
    if (codec or "").lower() in ("h265", "hevc"):
        cmd += ["-tag:v", "hvc1"]
    cmd.append(str(out))
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except OSError:
        return False
    return proc.returncode == 0 and out.exists() and out.stat().st_size > 0


def convert_raw(raw_path, mp4_path, keep_raw: bool = False, framerate: float | None = None) -> bool:
    """Convert a downloaded XM ``.raw`` recording to MP4 (video copied, audio encoded to AAC).

    The frame rate comes from *framerate* if given, else the recording's header
    rate (else 15). With audio, the video is additionally re-timed to the rate
    measured from the audio clock, so picture and sound stay in sync.

    Returns False if the file holds no video or ffmpeg fails (the raw file is
    kept in that case). On success the raw file is deleted unless ``keep_raw``.
    """
    raw_p = Path(raw_path)
    dem = XmDemuxer()
    video_part, audio_part = dem.feed(raw_p.read_bytes())
    video_tail, audio_tail = dem.finish()
    video, audio = video_part + video_tail, audio_part + audio_tail
    codec = dem.media_codec
    if not video:
        return False
    if codec is None:
        # The media byte did not map to a known codec (firmware-specific
        # value); fall back to inspecting the elementary stream itself.
        codec = codec_from_es(video)
    fps = framerate or dem.fps or 15
    track = (audio, dem.audio_codec, dem.audio_rate) if audio and dem.audio_codec and dem.audio_rate else None
    sync = dem.measured_fps if (track and not framerate) else None
    if not remux_to_mp4(video, codec, mp4_path, framerate=fps, audio=track, sync_fps=sync):
        return False
    if not keep_raw:
        raw_p.unlink()
    return True
