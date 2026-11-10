from xmcam import credentials, onvif
from xmcam.models import Credentials

IP = "192.0.2.10"
RTSP = f"rtsp://{IP}:554/user=rryp_password=TESTPASS_channel=0_stream=0&onvif=0.sdp?real_stream"


def _post(responses):
    def fake(url, body, body_ns="", timeout=6.0):
        for key, resp in responses.items():
            if key in body:
                return resp
        raise AssertionError(body)

    return fake


def test_recover_credentials(monkeypatch):
    monkeypatch.setattr(
        onvif,
        "soap_post",
        _post(
            {
                "GetProfiles": '<Profiles token="000"></Profiles>',
                "GetStreamUri": f"<tt:Uri>{RTSP}</tt:Uri>",
            }
        ),
    )
    creds = credentials.recover_credentials(IP)
    assert creds == Credentials(username="rryp", password="TESTPASS", source="onvif")


def test_recover_credentials_none(monkeypatch):
    monkeypatch.setattr(
        onvif,
        "soap_post",
        _post(
            {
                "GetProfiles": '<Profiles token="000"></Profiles>',
                "GetStreamUri": f"<tt:Uri>rtsp://{IP}:554/stream1</tt:Uri>",
            }
        ),
    )
    assert credentials.recover_credentials(IP) is None
