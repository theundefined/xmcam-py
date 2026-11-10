"""Test ONVIF GetSystemDateAndTime parsing (offline, transport mocked)."""
from __future__ import annotations

from datetime import datetime

from xmcam import onvif

_RESPONSE = """
<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>
<tt:UTCDateTime><tt:Time><tt:Hour>12</tt:Hour><tt:Minute>5</tt:Minute>
<tt:Second>30</tt:Second></tt:Time><tt:Date><tt:Year>2026</tt:Year>
<tt:Month>11</tt:Month><tt:Day>10</tt:Day></tt:Date></tt:UTCDateTime>
<tt:LocalDateTime><tt:Time><tt:Hour>13</tt:Hour><tt:Minute>5</tt:Minute>
<tt:Second>30</tt:Second></tt:Time><tt:Date><tt:Year>2026</tt:Year>
<tt:Month>11</tt:Month><tt:Day>10</tt:Day></tt:Date></tt:LocalDateTime>
</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>
"""


def test_get_system_time_prefers_local(monkeypatch):
    monkeypatch.setattr(onvif, "soap_post", lambda *a, **k: _RESPONSE)
    assert onvif.get_system_time("192.0.2.10") == datetime(2026, 11, 10, 13, 5, 30)


def test_get_system_time_none_on_empty(monkeypatch):
    monkeypatch.setattr(onvif, "soap_post", lambda *a, **k: "<empty/>")
    assert onvif.get_system_time("192.0.2.10") is None
