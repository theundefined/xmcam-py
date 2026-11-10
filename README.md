# xmcam

**Local-first Python library and CLI for Xiongmai (XM) IP cameras** — the kind
sold under countless brands and driven by the **iCSee** / **XMeye** mobile apps.
Discover cameras on your LAN, read their capabilities over ONVIF, and **list and
download the recording archive** straight off the SD card/disk over the native
**DVRIP** protocol. Everything happens on the local network: **no cloud, no
internet, no vendor account.**

> Distribution name: `xmcam-py` · import name: `xmcam`

## Why

These cameras store footage locally but the official way to get it off the
device is a clunky mobile app or a Windows-only VMS. There was no maintained
Python tool for pulling the **archive** programmatically. `xmcam` fills that
gap with a clean, dependency-free API and a scriptable CLI.

## Features

- **Discovery** — find cameras via ONVIF WS-Discovery, with Xiongmai detection.
- **Credential recovery** — these cameras answer ONVIF **anonymously** and leak
  the stream account in the RTSP URL; `xmcam` can recover the username/password
  the way the app does (on hardware you own — see *Security & ethics*).
- **Recording archive** — list recordings in any time range and download them,
  single files, arbitrary ranges or a whole day.
- **Transparent conversion** — the raw stream is Xiongmai's own container;
  `xmcam` demuxes it to an elementary H.264/H.265 stream and (with `ffmpeg`)
  remuxes to a playable **MP4** without re-encoding.
- **Live** — build RTSP URLs and grab JPEG snapshots.
- **PTZ** — pan/tilt/zoom over ONVIF where the hardware supports it.
- **Config backup** and **clock sync**.
- **Zero runtime dependencies** — pure standard library. `ffmpeg` is optional
  and only needed for MP4 output.

## Install

```bash
pip install xmcam-py
# optional, for MP4 output:  install ffmpeg via your system package manager
```

## Quick start (CLI)

Credentials are read from `--user/--password` or the `XMCAM_USER` / `XMCAM_PASS`
environment variables (preferred — they stay out of your shell history).

```bash
# Find cameras on the LAN (pass your host interface IP for reliable multicast)
xmcam discover --iface 192.0.2.5

# Recover the camera account from anonymous ONVIF (hardware you own)
xmcam creds 192.0.2.10

# List today's recordings
xmcam list 192.0.2.10 --start "2026-11-10 00:00:00" --end "2026-11-10 23:59:59"

# Download a one-hour range to MP4
export XMCAM_USER=... XMCAM_PASS=...
xmcam pull 192.0.2.10 --start "2026-11-10 08:00:00" --end "2026-11-10 09:00:00" --out ./footage
```

## Quick start (library)

```python
from xmcam.dvrip import DVRIPClient
from xmcam.files import list_recordings

with DVRIPClient("192.0.2.10", user="rryp", password="...") as cam:
    for rec in list_recordings(cam, "2026-11-10 08:00:00", "2026-11-10 09:00:00"):
        print(rec.begin_time, rec.length_bytes, rec.filename)
```

## Security & ethics

This library can recover camera credentials and pull footage over the LAN. That
is possible because the firmware answers ONVIF **without authentication** and
exposes the stream password in plaintext — a real weakness of these devices.
**Use `xmcam` only on cameras you own or are explicitly authorised to
administer.** It never contacts any cloud service and stores no credentials on
disk; supply them per-run via environment variables.

If you run these cameras: keep them off the internet (no P2P/cloud), put them on
an isolated VLAN, and firewall ports 8899/34567/554.

## Notes & limitations

- **Discovery is multicast-based.** Some firmwares (seen on XM535 / V5.01) do not
  answer WS-Discovery at all, even though they serve ONVIF and DVRIP. Such
  cameras won't appear in `discover` — address them by IP directly.
- **`stream-url` prints the password** inside the RTSP URL, because that is how
  these cameras authenticate; the URL is only usable with it. Treat that output
  as a secret (it may land in shell history/logs).
- **`info`**: several DVRIP info blocks (`SystemInfo`, `StorageInfo`, ...) are
  restricted for the auto-recovered account and come back empty; the command
  shows what is available plus the ONVIF device information.
- **Camera clock**: `synctime --show` reports whatever the camera believes the
  time is — which is often UTC unless you configured a timezone on the device.

## License

MIT © theundefined
