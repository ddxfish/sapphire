#!/usr/bin/env python3
"""Claude's seat at a chat room's door, as a stream: one line per cue.

Loops `room_wait` through tools/mcp-bridge.py (this persona's key) and prints
each cue the room hands this seat as ONE JSON line on stdout, so a Claude Code
Monitor wakes Claude to answer with `room_post` while he keeps working between
turns; parked here, the seat shows 'here' on the roster. A cue's pictures are
saved as files and their paths ride the line (Read shows them). Errors are
lines too (a Monitor must never go quiet on a failure).

    python tools/door-watch.py <room> [--images DIR]
"""
import argparse
import base64
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent / 'mcp-bridge.py'
WAIT_S = 120              # the longest one room_wait parks (core/rooms.py WAIT_MAX)
OPEN_S = 5                # my turn is open and the cue is in Claude's hands: look again this often


def call(name, args, timeout):
    msg = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
    out = subprocess.run([sys.executable, str(BRIDGE)], input=msg + '\n', capture_output=True, text=True, timeout=timeout)
    first = (out.stdout.strip().splitlines() or ['{}'])[0]
    answer = json.loads(first)
    if 'error' in answer:
        raise RuntimeError(answer['error'].get('message') or str(answer['error']))
    result = answer.get('result') or {}
    blocks = result.get('content') or []
    text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')
    if result.get('isError'):
        raise RuntimeError(text or 'the door said no')
    images = [b for b in blocks if b.get('type') == 'image']
    try:
        return json.loads(text) if text.lstrip().startswith('{') else {'note': text}, images
    except ValueError:
        return {'note': text}, images


def save(images, folder):
    paths = []
    for i, b in enumerate(images):
        ext = (b.get('mimeType') or 'image/jpeg').split('/')[-1].replace('jpeg', 'jpg')
        p = folder / f"{time.strftime('%H%M%S')}-{i}.{ext}"
        p.write_bytes(base64.b64decode(b.get('data') or ''))
        paths.append(str(p))
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('room')
    ap.add_argument('--images', help='folder for the pictures on a cue (default: a fresh temp folder)')
    a = ap.parse_args()
    folder = Path(a.images) if a.images else Path(tempfile.mkdtemp(prefix='door-'))
    folder.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'room': a.room, 'parked': True, 'images_dir': str(folder)}), flush=True)
    while True:
        try:
            r, images = call('room_wait', {'room': a.room, 'seconds': WAIT_S}, timeout=WAIT_S + 30)
        except Exception as e:
            print(json.dumps({'room': a.room, 'error': str(e)[:300]}), flush=True)
            time.sleep(10)
            continue
        if r.get('turn') and 'cue' in r:
            print(json.dumps({'room': a.room, 'turn': True, 'cue': r['cue'], 'images': save(images, folder),
                              'post_within_s': r.get('seconds'), 'about': r.get('about')}), flush=True)
            time.sleep(OPEN_S)
        elif r.get('turn'):
            print(json.dumps({'room': a.room, 'turn': True, 'open': True, 'note': r.get('note'),
                              'about': r.get('about')}), flush=True)   # a turn is open and the cue went elsewhere: say so
            time.sleep(OPEN_S)
        elif 'note' in r and not r.get('about'):
            print(json.dumps({'room': a.room, 'note': r['note']}), flush=True)   # the seat went, or the room: say so
            time.sleep(10)


if __name__ == '__main__':
    main()
