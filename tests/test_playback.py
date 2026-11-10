from datetime import datetime

import pytest
from conftest import build_frame, json_frame

from xmcam.dvrip import DVRIPClient
from xmcam.exceptions import DownloadError
from xmcam.models import Recording
from xmcam.playback import MEDIA_MSGID, download_recording, iter_recording

REC = Recording(
    filename="/idea0/2026-11-10/001/00.00.00-00.05.00[R].h264",
    begin_time=datetime(2026, 11, 10, 0, 0, 0),
    end_time=datetime(2026, 11, 10, 0, 5, 0),
    raw={"BeginTime": "2026-11-10 00:00:00", "EndTime": "2026-11-10 00:05:00"},
)


def _cam(factory, inbound):
    sock = factory(inbound)
    cam = DVRIPClient("192.0.2.10")
    cam.connect()
    cam.session = 0xAB
    return cam, sock


def test_download(fake_socket_factory, tmp_path):
    inbound = json_frame(1425, {"Ret": 100}) + build_frame(1426, b"AAAA") + build_frame(1426, b"BBBB")
    cam, sock = _cam(fake_socket_factory, inbound)
    seen = []
    out = tmp_path / "out.xmraw"
    n = download_recording(cam, REC, out, progress=seen.append)
    assert n == 8
    assert out.read_bytes() == b"AAAABBBB"
    assert seen == [4, 8]
    sent = bytes(sock.sent)
    assert b"Claim" in sent and b"DownloadStart" in sent and b"Stop" in sent
    assert b"0x000000AB" in sent
    assert b"ByName" in sent


def test_non_media_ignored(fake_socket_factory, tmp_path):
    inbound = (
        json_frame(1425, {"Ret": 100})
        + build_frame(1426, b"AAAA")
        + build_frame(1400, b"ctrl")
        + build_frame(1426, b"BBBB")
    )
    cam, _ = _cam(fake_socket_factory, inbound)
    out = tmp_path / "o.bin"
    assert download_recording(cam, REC, out) == 8
    assert out.read_bytes() == b"AAAABBBB"


def test_claim_busy(fake_socket_factory, tmp_path):
    cam, _ = _cam(fake_socket_factory, json_frame(1425, {"Ret": 103}))
    with pytest.raises(DownloadError, match="relogin"):
        download_recording(cam, REC, tmp_path / "x")


def test_iter_and_constants(fake_socket_factory):
    inbound = json_frame(1425, {"Ret": 100}) + build_frame(1426, b"ZZ")
    cam, _ = _cam(fake_socket_factory, inbound)
    assert list(iter_recording(cam, REC)) == [b"ZZ"]
    assert MEDIA_MSGID["DownloadStart"] == 1426
