#!/usr/bin/env python3
"""mpv-bridge — expose a local mpv JSON IPC socket to the LAN, safely.

Runs on the machine PLAYING the movie. The movie-watcher plugin (running on a
remote Sapphire) connects over TCP instead of the unix socket. Two safety
properties raw socat forwarding can't give you:

  1. Command allow-list. mpv's IPC includes `run`/`subprocess` (arbitrary
     command execution). The bridge only forwards read-only get_property for
     a fixed set of props, plus its own screenshot command. Everything else
     is rejected.
  2. Peer allow-list. Only IPs passed via --allow may connect.

It also solves the screenshot problem: `screenshot-to-file` writes to THIS
machine's disk, which a remote daemon can't read. The bridge's virtual
command `["x-screenshot", "<mode>"]` does the file dance locally and returns
the JPEG as base64 in the JSON reply.

Usage (movie night):
    python3 mpv-bridge.py --allow 192.168.0.206
    # defaults: --port 9877, --socket /tmp/mpvsocket, --bind 0.0.0.0

Stdlib only — copy this single file anywhere, no Sapphire install needed.
"""

import argparse
import base64
import json
import logging
import os
import socket
import tempfile
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("mpv-bridge")

ALLOWED_PROPS = {"time-pos", "core-idle", "filename", "sub-text", "media-title", "pause", "duration"}
SCREENSHOT_MODES = {"video", "subtitles", "window"}


def mpv_roundtrip(sock, buf, command, rid):
    """Send one command to mpv's unix socket, return (reply, leftover_buf).
    Matches on request_id so interleaved async events aren't taken as the reply."""
    sock.sendall((json.dumps({"command": command, "request_id": rid}) + "\n").encode())
    while True:
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("request_id") == rid:
                return msg, buf
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("mpv socket closed")
        buf += chunk


def do_screenshot(mpv, buf, mode, rid):
    """Run screenshot-to-file locally, return (reply_for_client, leftover_buf)."""
    fd, path = tempfile.mkstemp(prefix="mpv-bridge-", suffix=".jpg")
    os.close(fd)
    try:
        msg, buf = mpv_roundtrip(mpv, buf, ["screenshot-to-file", path, mode], rid)
        if msg.get("error") != "success":
            return {"error": f"screenshot failed: {msg.get('error')}"}, buf
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return {"error": "success", "data": b64}, buf
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def handle_client(conn, addr, mpv_path):
    """One TCP client gets its own private unix connection to mpv."""
    try:
        mpv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        mpv.settimeout(10)
        mpv.connect(mpv_path)
    except OSError as e:
        log.warning(f"{addr[0]}: mpv socket unavailable ({e})")
        conn.close()
        return
    log.info(f"{addr[0]}: connected")
    mpv_buf = b""
    tcp_buf = b""
    internal_rid = 10**9  # separate id space for bridge-issued commands
    try:
        while True:
            while b"\n" not in tcp_buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                tcp_buf += chunk
            line, tcp_buf = tcp_buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                req = json.loads(line)
                cmd = req.get("command") or []
            except Exception:
                req = None
                cmd = []
            rid = req.get("request_id") if isinstance(req, dict) else None
            reply = {"error": "command not allowed by bridge"}

            if cmd and cmd[0] == "get_property" and len(cmd) == 2 and cmd[1] in ALLOWED_PROPS:
                reply, mpv_buf = mpv_roundtrip(mpv, mpv_buf, cmd, rid or 0)
            elif cmd and cmd[0] == "x-screenshot":
                mode = cmd[1] if len(cmd) > 1 and cmd[1] in SCREENSHOT_MODES else "video"
                internal_rid += 1
                reply, mpv_buf = do_screenshot(mpv, mpv_buf, mode, internal_rid)
            else:
                log.warning(f"{addr[0]}: rejected command {cmd[:1]}")

            reply["request_id"] = rid
            conn.sendall((json.dumps(reply) + "\n").encode())
    except (ConnectionError, OSError) as e:
        log.info(f"{addr[0]}: disconnected ({e})")
    finally:
        conn.close()
        mpv.close()


def main():
    ap = argparse.ArgumentParser(description="LAN bridge to a local mpv IPC socket")
    ap.add_argument("--allow", action="append", required=True, metavar="IP",
                    help="peer IP allowed to connect (repeatable)")
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--socket", default="/tmp/mpvsocket", help="mpv --input-ipc-server path")
    args = ap.parse_args()
    allowed = set(args.allow)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(4)
    log.info(f"listening on {args.bind}:{args.port} -> {args.socket} (allowed: {', '.join(sorted(allowed))})")
    while True:
        conn, addr = srv.accept()
        if addr[0] not in allowed:
            log.warning(f"{addr[0]}: REFUSED (not in allow-list)")
            conn.close()
            continue
        threading.Thread(target=handle_client, args=(conn, addr, args.socket), daemon=True).start()


if __name__ == "__main__":
    main()
