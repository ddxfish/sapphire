"""End-to-end tests against the LIVE dev Sapphire, in a real Chromium.

Run from the project root, outside pytest's testpaths (so health.sh never
collects them):  conda run -n sapphire python -m pytest tools/e2e -q

- The page logs in with a cookie minted like /login's (core.auth), so no password.
- API checks use the health token (user/sapphire-health.token).
- `cut` is a TCP relay between the page and Sapphire that can drop every
  connection (a network drop) or refuse new ones (a dead network), while the
  server itself stays up.
- The fake mic plays silence: no turn runs, nothing lands in a chat.
"""
import asyncio
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from core.ssl_utils import spki_pin

PORT = int(getattr(config, 'WEB_UI_PORT', 8073))
SCHEME = 'https' if getattr(config, 'WEB_UI_SSL_ADHOC', True) else 'http'
BASE = f'{SCHEME}://localhost:{PORT}'


def api(path, method='GET', body=None):
    token = (ROOT / 'user' / 'sapphire-health.token').read_text().strip()
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=10) as r:
        return json.loads(r.read() or b'null')


def wait_until(check, seconds, step=0.25, page=None):
    """Poll `check`. Pass the page while waiting on its events: sync Playwright
    delivers them only inside its own calls, never during time.sleep."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        page.wait_for_timeout(step * 1000) if page else time.sleep(step)
    return False


def server_log(since):
    """Sapphire's journal lines since `since` (epoch), or None off systemd."""
    try:
        out = subprocess.run(['journalctl', '--user', '-u', 'sapphire', '--since', f'@{int(since)}',
                              '--no-pager', '-o', 'cat'], capture_output=True, text=True, timeout=10)
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


class Relay:
    """localhost:<port> -> Sapphire, byte for byte (TLS passes through)."""

    def __init__(self):
        self.lsock = socket.create_server(('127.0.0.1', 0))
        self.port = self.lsock.getsockname()[1]
        self.url = f'{SCHEME}://localhost:{self.port}'
        self.pairs = []
        self.refusing = False
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.lsock.accept()
            except OSError:
                return
            if self.refusing:
                _reset(c)
                continue
            try:
                u = socket.create_connection(('127.0.0.1', PORT))
            except OSError:
                _reset(c)
                continue
            with self.lock:
                self.pairs.append((c, u))
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=_pipe, args=(a, b), daemon=True).start()

    def drop(self):
        """Every live connection dies at once, like a network drop."""
        with self.lock:
            pairs, self.pairs = self.pairs, []
        for c, u in pairs:
            _reset(c)
            _reset(u)

    def close(self):
        self.lsock.close()
        self.drop()


def _reset(s):
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))   # RST, not FIN
        s.close()
    except OSError:
        pass


def _pipe(a, b):
    try:
        while data := a.recv(65536):
            b.sendall(data)
        b.shutdown(socket.SHUT_WR)          # pass the FIN on: a server waits for it after a close frame
    except OSError:
        pass


@pytest.fixture(scope='session')
def silence(tmp_path_factory):
    path = tmp_path_factory.mktemp('mic') / 'silence.wav'
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\0\0' * 16000 * 5)
    return path


@pytest.fixture
def relay():
    r = Relay()
    yield r
    r.close()


@pytest.fixture
def page(silence):
    from playwright.sync_api import sync_playwright
    from core.auth import mint_session_cookie
    try:
        api('/api/runtime/true-speech')
    except Exception as e:
        pytest.skip(f'dev Sapphire is not reachable at {BASE}: {e}')
    args = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
            f'--use-file-for-fake-audio-capture={silence}', '--autoplay-policy=no-user-gesture-required']
    pin = spki_pin()
    if pin:
        args.append(f'--ignore-certificate-errors-spki-list={pin}')
    with sync_playwright() as p:
        browser = p.chromium.launch(args=args, headless=not os.environ.get('E2E_HEADED'))
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
        ctx.add_cookies([{'name': 'sapphire_session', 'value': mint_session_cookie(),
                          'domain': 'localhost', 'path': '/', 'httpOnly': True, 'sameSite': 'Lax'}])
        pg = ctx.new_page()
        pg.errors = []
        pg.on('pageerror', lambda e: pg.errors.append(str(e)))
        yield pg
        browser.close()
