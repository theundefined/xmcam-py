"""Command-line interface for :mod:`xmcam`.

Subcommands: ``discover``, ``creds``, ``info``, ``list``, ``pull``,
``snapshot``, ``stream-url``, ``ptz``, ``config``, ``synctime``.

Credentials come from ``--user/--password`` or the ``XMCAM_USER`` / ``XMCAM_PASS``
environment variables. When no password is available, DVRIP subcommands try to
recover the camera account from anonymous ONVIF automatically (the same trick
the iCSee app uses) -- only useful on hardware you own.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .dvrip import DVRIP_PORT, DVRIPClient
from .exceptions import XMCamError
from .models import TIME_FMT


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _resolve_credentials(host: str, user: Optional[str], password: Optional[str]) -> Tuple[str, str, str]:
    """Return ``(user, password, source)`` using args, env, then ONVIF recovery."""
    user = user or os.environ.get("XMCAM_USER")
    if password is None:
        password = os.environ.get("XMCAM_PASS")

    if password:
        return (user or "admin"), password, "manual"

    # No password yet: recover the stream account from anonymous ONVIF.
    try:
        from .credentials import recover_credentials

        creds = recover_credentials(host)
        if creds:
            return creds.username, creds.password, "onvif"
    except XMCamError:
        pass
    return (user or "admin"), (password or ""), "manual"


def _connect(args) -> DVRIPClient:
    user, password, source = _resolve_credentials(args.host, args.user, args.password)
    if source == "onvif":
        print(f"[{args.host}] recovered credentials from anonymous ONVIF (user={user})", file=sys.stderr)
    cam = DVRIPClient(args.host, user=user, password=password, port=args.port)
    if not cam.login():
        from .dvrip import ret_meaning

        sys.exit(f"[{args.host}] DVRIP login failed ({ret_meaning(cam.last_login_ret)}).")
    return cam


def _safe_name(rec, host: str) -> str:
    begin = rec.begin_time.strftime("%Y%m%d_%H%M%S") if rec.begin_time else "rec"
    end = rec.end_time.strftime("%H%M%S") if rec.end_time else ""
    base = (rec.filename or "rec").split("/")[-1]
    for ext in (".h264", ".h265", ".jpg"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    name = f"{host}_{begin}-{end}_{base}"
    for ch in "[]":
        name = name.replace(ch, "")
    return name


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_discover(args) -> None:
    from .discovery import discover

    cameras = discover(args.iface, args.timeout)
    if not cameras:
        print("No ONVIF cameras found.")
        return
    for cam in cameras:
        tag = "  [Xiongmai / DVRIP 34567]" if cam.is_xiongmai else ""
        print(f"{cam.ip:16} {cam.xaddr}{tag}")
        if cam.mac:
            print(f"{'':16} mac={cam.mac}")


def cmd_creds(args) -> None:
    from .credentials import recover_credentials

    creds = recover_credentials(args.host, port=args.onvif_port)
    if not creds:
        sys.exit(f"[{args.host}] could not recover credentials from ONVIF.")
    print(f"user={creds.username}")
    print(f"password={creds.password}")
    print(f"source={creds.source}")


def cmd_info(args) -> None:
    cam = _connect(args)
    try:
        for key in ("SystemInfo", "General", "OEMInfo", "StorageInfo"):
            try:
                info = cam.get_info(key)
            except XMCamError:
                continue
            if info:  # several blocks are restricted for the auto account -> skip
                print(f"=== {key} ===")
                print(info)
    finally:
        cam.close()
    # Anonymous ONVIF identity (best effort).
    try:
        from .onvif import get_device_information

        info = get_device_information(args.host, port=args.onvif_port)
        print("=== ONVIF DeviceInformation ===")
        print(f"  {info.manufacturer} {info.model} fw={info.firmware} serial={info.serial}")
    except XMCamError:
        pass


def cmd_list(args) -> None:
    from .files import list_recordings_range

    cam = _connect(args)
    try:
        recs = list_recordings_range(
            cam, args.start, args.end, channel=args.channel, filetype=args.type, stream=args.stream
        )
        total = sum(r.length_bytes for r in recs)
        for r in recs:
            begin = r.begin_time.strftime(TIME_FMT) if r.begin_time else "?"
            end = r.end_time.strftime("%H:%M:%S") if r.end_time else "?"
            print(f"{begin} -> {end}  {_human(r.length_bytes):>9}  {r.filename}")
        print(f"\nTotal: {len(recs)} file(s), {_human(total)}")
    finally:
        cam.close()


def cmd_pull(args) -> None:
    from .files import list_recordings_range
    from .media import convert_raw, have_ffmpeg
    from .playback import download_recording

    cam = _connect(args)
    outdir = Path(args.out).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        recs = list_recordings_range(
            cam, args.start, args.end, channel=args.channel, filetype=args.type, stream=args.stream
        )
        if args.grep:
            recs = [r for r in recs if args.grep in (r.filename or "")]
        if not recs:
            print("No recordings in the given range.")
            return

        to_mp4 = not args.raw_only and not args.no_convert
        if to_mp4 and not have_ffmpeg():
            print("WARNING: ffmpeg not found - saving raw XM streams (.xmraw), no MP4.")
            to_mp4 = False

        total = sum(r.length_bytes for r in recs)
        print(f"To download: {len(recs)} file(s), ~{_human(total)} -> {outdir}")
        done = 0
        for i, rec in enumerate(recs, 1):
            name = _safe_name(rec, args.host)
            final = outdir / (name + (".mp4" if to_mp4 else ".xmraw"))
            if final.exists() and final.stat().st_size > 0 and not args.force:
                print(f"[{i}/{len(recs)}] skip (exists) {final.name}")
                done += 1
                continue
            raw_path = outdir / (name + ".xmraw")
            if i > 1:
                cam.relogin()  # fresh connection per file (XM reserves the channel)

            size = rec.length_bytes

            def progress(written: int, _size=size, _i=i, _name=name) -> None:
                pct = (written / _size * 100) if _size else 0
                sys.stdout.write(f"\r[{_i}/{len(recs)}] {_name}  {_human(written)} ({pct:4.0f}%)")
                sys.stdout.flush()

            t0 = time.time()
            try:
                written = download_recording(
                    cam, rec, raw_path, progress=progress, channel=args.channel, stream=args.stream
                )
                dt = time.time() - t0
                rate = f"{_human(written / dt)}/s" if dt else ""
                if to_mp4:
                    ok = convert_raw(raw_path, final, keep_raw=args.keep_raw)
                    status = "OK" if ok else "RAW (convert failed)"
                else:
                    status = "OK"
                print(f"\r[{i}/{len(recs)}] {status} {final.name}  {_human(written)} in {dt:.1f}s  {rate}        ")
                done += 1
            except XMCamError as exc:
                print(f"\r[{i}/{len(recs)}] ERROR {name}: {exc}")
        print(f"\nDownloaded {done}/{len(recs)} file(s) to {outdir}")
    finally:
        cam.close()


def cmd_snapshot(args) -> None:
    from .live import fetch_snapshot

    user, password, _ = _resolve_credentials(args.host, args.user, args.password)
    data = fetch_snapshot(args.host, user, password, channel=args.channel)
    out = Path(args.out) if args.out else Path(f"{args.host}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
    out.write_bytes(data)
    print(f"Saved {len(data)} bytes -> {out}")


def cmd_stream_url(args) -> None:
    from .onvif import get_stream_uris

    uris = get_stream_uris(args.host, port=args.onvif_port)
    if not uris:
        sys.exit(f"[{args.host}] no stream URIs returned.")
    for su in uris:
        print(su.uri)


def cmd_ptz(args) -> None:
    from .ptz import ptz_move

    cam = _connect(args)
    try:
        reply = ptz_move(cam, args.direction, channel=args.channel, step=args.step)
        print(f"PTZ {args.direction}: Ret={reply.get('Ret')}")
    finally:
        cam.close()


def cmd_config(args) -> None:
    from .config_backup import save_config

    cam = _connect(args)
    try:
        data = save_config(cam, args.out)
        print(f"Exported {len(data)} config block(s) -> {args.out}")
    finally:
        cam.close()


def cmd_convert(args) -> None:
    from .media import convert_raw, have_ffmpeg

    if not have_ffmpeg():
        sys.exit("ffmpeg not found - install it to convert to MP4.")
    src = Path(args.input)
    if not src.exists():
        sys.exit(f"input not found: {src}")
    out = Path(args.out) if args.out else src.with_suffix(".mp4")
    ok = convert_raw(src, out, keep_raw=True, framerate=args.framerate)
    if not ok:
        sys.exit(f"conversion failed (no video stream in {src}?)")
    print(f"Converted -> {out}")


def cmd_synctime(args) -> None:
    from .timesync import get_time, set_time, sync_to_host

    cam = _connect(args)
    try:
        if args.show:
            print(f"Camera time: {get_time(cam, onvif_port=args.onvif_port)}")
            return
        if args.set:
            when = datetime.strptime(args.set, TIME_FMT)
            reply = set_time(cam, when)
        else:
            reply = sync_to_host(cam)
        print(f"Set time: Ret={reply.get('Ret')}")
    finally:
        cam.close()


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xmcam",
        description="Local-first toolkit for Xiongmai / iCSee / XMeye IP cameras (no cloud).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_auth(sp, onvif=False):
        sp.add_argument("host", help="camera IP address")
        sp.add_argument("--port", type=int, default=DVRIP_PORT, help="DVRIP port (default 34567)")
        sp.add_argument("--user", help="username (or $XMCAM_USER)")
        sp.add_argument("--password", help="password (or $XMCAM_PASS)")
        sp.add_argument("--onvif-port", type=int, default=8899, dest="onvif_port")

    d = sub.add_parser("discover", help="find ONVIF cameras on the LAN")
    d.add_argument("--iface", help="local interface IP for multicast (e.g. 192.0.2.5)")
    d.add_argument("--timeout", type=float, default=5.0)
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("creds", help="recover the camera account from anonymous ONVIF")
    c.add_argument("host")
    c.add_argument("--onvif-port", type=int, default=8899, dest="onvif_port")
    c.set_defaults(func=cmd_creds)

    i = sub.add_parser("info", help="show camera/device information")
    add_auth(i)
    i.set_defaults(func=cmd_info)

    today = datetime.now().strftime("%Y-%m-%d")
    for name, helptext, func in (("list", "list recordings", cmd_list), ("pull", "download recordings", cmd_pull)):
        sp = sub.add_parser(name, help=helptext)
        add_auth(sp)
        sp.add_argument("--start", default=f"{today} 00:00:00", help='"YYYY-MM-DD HH:MM:SS"')
        sp.add_argument("--end", default=f"{today} 23:59:59", help='"YYYY-MM-DD HH:MM:SS"')
        sp.add_argument("--channel", type=int, default=0)
        sp.add_argument("--type", default="h264", choices=["h264", "jpg"])
        sp.add_argument("--stream", default="Main", choices=["Main", "Sub"])
        if name == "pull":
            sp.add_argument("--out", default="./recordings", help="output directory")
            sp.add_argument("--grep", help="only files whose name contains this fragment")
            sp.add_argument("--force", action="store_true", help="overwrite existing files")
            sp.add_argument("--no-convert", action="store_true", help="keep raw .xmraw, no MP4")
            sp.add_argument("--raw-only", action="store_true", help="download raw stream only")
            sp.add_argument("--keep-raw", action="store_true", help="keep .xmraw next to the MP4")
        sp.set_defaults(func=func)

    s = sub.add_parser("snapshot", help="grab a JPEG snapshot")
    add_auth(s)
    s.add_argument("--channel", type=int, default=0)
    s.add_argument("--out", help="output file (default <host>_<timestamp>.jpg)")
    s.set_defaults(func=cmd_snapshot)

    su = sub.add_parser("stream-url", help="print RTSP stream URLs (via anonymous ONVIF)")
    su.add_argument("host")
    su.add_argument("--onvif-port", type=int, default=8899, dest="onvif_port")
    su.set_defaults(func=cmd_stream_url)

    pt = sub.add_parser("ptz", help="PTZ control (where supported)")
    add_auth(pt)
    pt.add_argument("direction", choices=["up", "down", "left", "right", "zoomin", "zoomout"])
    pt.add_argument("--channel", type=int, default=0)
    pt.add_argument("--step", type=int, default=4)
    pt.set_defaults(func=cmd_ptz)

    cfg = sub.add_parser("config", help="export camera configuration to a JSON file")
    add_auth(cfg)
    cfg.add_argument("--out", default="xmcam-config.json", help="output JSON file")
    cfg.set_defaults(func=cmd_config)

    cv = sub.add_parser("convert", help="convert a local .xmraw recording to MP4 (no camera needed)")
    cv.add_argument("input", help="path to a downloaded .xmraw file")
    cv.add_argument("--out", help="output MP4 path (default: same name with .mp4)")
    cv.add_argument("--framerate", type=int, default=15)
    cv.set_defaults(func=cmd_convert)

    st = sub.add_parser("synctime", help="read or set the camera clock")
    add_auth(st)
    st.add_argument("--set", help='set this time ("YYYY-MM-DD HH:MM:SS") instead of now')
    st.add_argument("--show", action="store_true", help="only show the current camera time")
    st.set_defaults(func=cmd_synctime)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
    except XMCamError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
