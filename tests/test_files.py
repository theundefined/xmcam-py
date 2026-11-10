from datetime import datetime

import pytest
from conftest import json_frame

from xmcam.dvrip import DVRIPClient
from xmcam.exceptions import ProtocolError
from xmcam.files import list_recordings, list_recordings_range

E1 = {
    "FileName": "/idea0/2026-11-10/001/00.00.00-00.05.00[R][@1][0].h264",
    "BeginTime": "2026-11-10 00:00:00",
    "EndTime": "2026-11-10 00:05:00",
    "FileLength": "0x0000c3cc",
    "DiskNo": 0,
    "SerialNo": 1,
}
E2 = {
    "FileName": "/idea0/2026-11-10/001/00.05.00-00.10.00[R][@2][0].h264",
    "BeginTime": "2026-11-10 00:05:00",
    "EndTime": "2026-11-10 00:10:00",
    "FileLength": "0x0000c3cc",
}


def _cam(factory, inbound):
    sock = factory(inbound)
    cam = DVRIPClient("192.0.2.10")
    cam.connect()
    cam.session = 0x1234
    return cam, sock


def test_list_recordings(fake_socket_factory):
    cam, sock = _cam(
        fake_socket_factory,
        json_frame(1441, {"Ret": 100, "Name": "OPFileQuery", "OPFileQuery": [E1, E2, {"BeginTime": "x"}]}),
    )
    recs = list_recordings(cam, datetime(2026, 11, 10), "2026-11-10 01:00:00")
    assert len(recs) == 2
    assert recs[0].filename == E1["FileName"]
    assert recs[0].begin_time == datetime(2026, 11, 10, 0, 0, 0)
    assert recs[0].length_bytes == 0xC3CC * 1024
    assert recs[1].end_time == datetime(2026, 11, 10, 0, 10, 0)
    assert b"OPFileQuery" in bytes(sock.sent)
    assert b"2026-11-10 00:00:00" in bytes(sock.sent)
    assert b"0x00000000" in bytes(sock.sent)


def test_sub_stream_mask(fake_socket_factory):
    cam, sock = _cam(fake_socket_factory, json_frame(1441, {"Ret": 100, "OPFileQuery": None}))
    list_recordings(cam, "2026-11-10 00:00:00", "2026-11-10 01:00:00", stream="Sub")
    assert b"0x00000001" in bytes(sock.sent)


def test_empty(fake_socket_factory):
    cam, _ = _cam(fake_socket_factory, json_frame(1441, {"Ret": 100, "OPFileQuery": None}))
    assert list_recordings(cam, "2026-11-10 00:00:00", "2026-11-10 01:00:00") == []


def test_bad_ret(fake_socket_factory):
    cam, _ = _cam(fake_socket_factory, json_frame(1441, {"Ret": 607}))
    with pytest.raises(ProtocolError):
        list_recordings(cam, "2026-11-10 00:00:00", "2026-11-10 01:00:00")


E3 = {
    "FileName": "/idea0/2026-11-10/001/00.10.00-00.15.00[R][@3][0].h264",
    "BeginTime": "2026-11-10 00:10:00",
    "EndTime": "2026-11-10 00:15:00",
    "FileLength": "0x0000c3cc",
}


def test_range_paginates_and_dedups(fake_socket_factory):
    # page_limit=2: a full page (2 entries) triggers another query starting at
    # the last EndTime; pagination stops on the short final page. E2 repeats
    # across pages and must be de-duplicated.
    replies = (
        json_frame(1441, {"Ret": 100, "OPFileQuery": [E1, E2]})  # full page -> paginate
        + json_frame(1441, {"Ret": 100, "OPFileQuery": [E2, E3]})  # full page -> paginate
        + json_frame(1441, {"Ret": 100, "OPFileQuery": [E3]})  # short page -> stop
    )
    cam, sock = _cam(fake_socket_factory, replies)
    recs = list_recordings_range(cam, "2026-11-10 00:00:00", "2026-11-11 12:00:00", page_limit=2)
    assert [r.filename for r in recs] == [E1["FileName"], E2["FileName"], E3["FileName"]]
    # At least three queries were issued, and a later one advanced the cursor
    # to the previous page's last EndTime.
    assert bytes(sock.sent).count(b'OPFileQuery",') >= 3
    assert b"2026-11-10 00:10:00" in bytes(sock.sent)
