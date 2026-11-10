"""Tests for the live-view URL builders (pure string construction, offline)."""
from __future__ import annotations

from xmcam import live
from xmcam.onvif import parse_rtsp_credentials


def test_rtsp_url_embeds_credentials():
    url = live.rtsp_url("192.0.2.10", "rryp", "TESTPASS", channel=0, stream=0)
    assert url.startswith("rtsp://192.0.2.10:554/")
    assert "user=rryp" in url
    assert "password=TESTPASS" in url
    # Round-trips through the ONVIF credential parser.
    assert parse_rtsp_credentials(url) == ("rryp", "TESTPASS")


def test_rtsp_url_sub_stream():
    url = live.rtsp_url("192.0.2.10", "u", "p", stream=1)
    assert "stream=1" in url


def test_snapshot_url():
    url = live.snapshot_url("192.0.2.10", "rryp", "TESTPASS", channel=0)
    assert url.startswith("http://192.0.2.10:80/webcapture.jpg?")
    assert "command=snap" in url
    assert "user=rryp" in url
    assert "password=TESTPASS" in url


def test_parse_rtsp_credentials_with_underscore_password():
    # Password containing an underscore must not be truncated.
    url = "rtsp://192.0.2.10:554/user=rryp_password=ab_cd12_channel=0_stream=0&onvif=0.sdp"
    assert parse_rtsp_credentials(url) == ("rryp", "ab_cd12")


def test_parse_rtsp_credentials_none_without_creds():
    assert parse_rtsp_credentials("rtsp://192.0.2.10:554/live") is None
