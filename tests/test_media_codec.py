"""Content-based codec detection (firmware-independent)."""

from __future__ import annotations

from xmcam.media import codec_from_es, demux


def _sc(nal_byte: bytes, body: bytes = b"\x00" * 8) -> bytes:
    return b"\x00\x00\x01" + nal_byte + body


def test_detect_h265_from_vps():
    # HEVC VPS NAL header first byte = (32 << 1) = 0x40.
    es = _sc(b"\x40") + _sc(b"\x42")  # VPS then SPS
    assert codec_from_es(es) == "h265"


def test_detect_h264_from_sps():
    # H.264 SPS NAL byte 0x67 (nal_ref_idc=3, type=7).
    es = _sc(b"\x67") + _sc(b"\x68")  # SPS then PPS
    assert codec_from_es(es) == "h264"


def test_detect_none_on_garbage():
    assert codec_from_es(b"not a stream at all") is None


def test_demux_falls_back_to_es_detection():
    """An I-frame whose media byte is unknown (e.g. 83) still yields a codec."""
    import struct

    # 0xFC I-frame header <BBBBII with media=83 (unknown), length = len(payload).
    payload = b"\x00\x00\x01\x40\x01\x02\x03\x04"  # looks like an HEVC VPS NAL
    header = struct.pack("<BBBBII", 83, 12, 32, 68, 0, len(payload))
    blob = b"\x00\x00\x01\xfc" + header + payload
    video, _audio, codec = demux(blob)
    assert video == payload
    assert codec == "h265"
