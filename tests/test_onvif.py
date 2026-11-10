import pytest

from xmcam import onvif
from xmcam.exceptions import OnvifError

IP = "192.0.2.10"
RTSP = f"rtsp://{IP}:554/user=rryp_password=TESTPASS_channel=0_stream=0&amp;onvif=0.sdp?real_stream"

DEVICE_INFO = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>'
    "<tds:GetDeviceInformationResponse xmlns:tds=\"http://www.onvif.org/ver10/device/wsdl\">"
    "<tds:Manufacturer>XM</tds:Manufacturer><tds:Model>IPC-Test</tds:Model>"
    "<tds:FirmwareVersion>V5.00.R02</tds:FirmwareVersion>"
    "<tds:SerialNumber>SN0000</tds:SerialNumber><tds:HardwareId>HW1</tds:HardwareId>"
    "</tds:GetDeviceInformationResponse></s:Body></s:Envelope>"
)
USERS = (
    "<s:Envelope><s:Body><tds:GetUsersResponse>"
    "<tds:User><tt:Username>rryp</tt:Username><tt:UserLevel>Administrator</tt:UserLevel></tds:User>"
    "</tds:GetUsersResponse></s:Body></s:Envelope>"
)
SERVICES = (
    "<s:Envelope><s:Body><tds:GetServicesResponse>"
    "<tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace></tds:Service>"
    "<tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace></tds:Service>"
    "</tds:GetServicesResponse></s:Body></s:Envelope>"
)
PROFILES = (
    "<s:Envelope><s:Body><trt:GetProfilesResponse>"
    '<trt:Profiles token="000" fixed="true"><tt:Name>main</tt:Name></trt:Profiles>'
    '<trt:Profiles token="001" fixed="true"><tt:Name>sub</tt:Name></trt:Profiles>'
    "</trt:GetProfilesResponse></s:Body></s:Envelope>"
)
STREAM = (
    "<s:Envelope><s:Body><trt:GetStreamUriResponse><trt:MediaUri>"
    f"<tt:Uri>{RTSP}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse></s:Body></s:Envelope>"
)
SNAP = (
    "<s:Envelope><s:Body><trt:GetSnapshotUriResponse><trt:MediaUri>"
    f"<tt:Uri>http://{IP}:80/snap.jpg</tt:Uri></trt:MediaUri></trt:GetSnapshotUriResponse>"
    "</s:Body></s:Envelope>"
)


def fake_post(url, body, body_ns="", timeout=6.0):
    for key, resp in (
        ("GetDeviceInformation", DEVICE_INFO),
        ("GetUsers", USERS),
        ("GetServices", SERVICES),
        ("GetProfiles", PROFILES),
        ("GetStreamUri", STREAM),
        ("GetSnapshotUri", SNAP),
    ):
        if key in body:
            return resp
    raise AssertionError("unexpected request: " + body)


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    monkeypatch.setattr(onvif, "soap_post", fake_post)


def test_device_information():
    info = onvif.get_device_information(IP)
    assert info.manufacturer == "XM"
    assert info.model == "IPC-Test"
    assert info.firmware == "V5.00.R02"
    assert info.serial == "SN0000"
    assert info.hardware_id == "HW1"


def test_users_and_services():
    assert onvif.get_users(IP) == [{"Username": "rryp", "UserLevel": "Administrator"}]
    assert onvif.get_services(IP) == [
        "http://www.onvif.org/ver10/device/wsdl",
        "http://www.onvif.org/ver10/media/wsdl",
    ]


def test_stream_uris_extract_credentials():
    uris = onvif.get_stream_uris(IP)
    assert [u.profile for u in uris] == ["000", "001"]
    assert uris[0].kind == "rtsp"
    assert uris[0].username == "rryp"
    assert uris[0].password == "TESTPASS"
    assert "&amp;" not in uris[0].uri


def test_snapshot_uris():
    uris = onvif.get_snapshot_uris(IP)
    assert len(uris) == 2 and uris[0].kind == "snapshot"
    assert uris[0].username is None


def test_parse_rtsp_credentials():
    assert onvif.parse_rtsp_credentials(
        f"rtsp://{IP}:554/user=rryp_password=TESTPASS_channel=0_stream=0"
    ) == ("rryp", "TESTPASS")
    assert onvif.parse_rtsp_credentials(f"rtsp://{IP}:554/stream1") is None


def test_soap_fault_raises(monkeypatch):
    monkeypatch.setattr(
        onvif, "soap_post", lambda *a, **k: "<s:Envelope><s:Body><s:Fault><s:Reason><s:Text>nope</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"
    )
    with pytest.raises(OnvifError):
        onvif.get_device_information(IP)
