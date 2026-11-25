"""The one door for a Hugging Face download a person waits on.

fetch() runs the download in a child (core/hf_fetch_worker.py) and watches it
move. Hugging Face serves a large file two ways: the classic one (plain HTTPS
from its CDN) and the newer xet transfer, which talks to a second server. A
router that had cached dead addresses for that second server held a first
Whisper download at 0% for 19 minutes, three silent tries in a row
(2026-11-24). Here a path that stops moving for STALL_S is ended and the
other one tried; when both fail the reason comes from core/net_check.py, in
words a person can act on.

The classic path goes first: its requests time out in seconds and it was the
one that worked. sapphire.py makes the same choice for every other hub call
(HF_HUB_DISABLE_XET, a user's own value wins there); this door sets it per
child, so it can still try xet second.

Callers look in the cache themselves first (each already did); this is for
what is really missing. The row is the caller's: pass its handle
(core/notify.py progress()) and it gets byte words, the stall, and the cause.
"""
import importlib.util
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).absolute().parent.parent
WORKER = ROOT / 'core' / 'hf_fetch_worker.py'
LOG = ROOT / 'user' / 'logs' / 'hf_fetch.log'
HUB = 'huggingface.co'
START_S = 90                # for the child to import the hub and say hello (a cold disk, a virus scanner)
STALL_S = 20                # no byte and no word from the child for this long: that path is not moving
_FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
_locks: dict = {}           # repo -> lock: two callers wanting one model make one download
_guard = threading.Lock()


class FetchError(RuntimeError):
    """The download did not land. str() is for a person; verdict is net_check's."""

    def __init__(self, words, verdict='unknown'):
        super().__init__(words)
        self.verdict = verdict


class _Quiet:
    """The handle of a caller with no row."""
    id = None

    def name(self, what): pass
    def bytes(self, done, total=None): pass


def _routes():
    """(name, HF_HUB_DISABLE_XET for the child), the first tried first."""
    out = [('classic', '1')]
    if importlib.util.find_spec('hf_xet') is not None:
        out.append(('xet', '0'))
    return out


def _open_log():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    keep = LOG.exists() and LOG.stat().st_size < 1024 * 1024
    return open(LOG, 'a' if keep else 'w', encoding='utf-8')


def _pump(proc, inbox):
    for line in proc.stdout:
        try:
            inbox.put(json.loads(line))
        except ValueError:
            pass
    inbox.put(None)


def _stop(proc):
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception as e:
        logger.warning(f"[FETCH] the download process did not stop: {e}")
    for pipe in (proc.stdin, proc.stdout):
        try:
            pipe.close()
        except Exception:
            pass


def _run(job, xet_off, handle, stall, hosts):
    """One path, start to end. ('ok', path) | ('stalled', seconds) | ('error', words) | ('fatal', words)."""
    env = dict(os.environ, PYTHONUNBUFFERED='1', HF_HUB_DISABLE_XET=xet_off)
    with _open_log() as log:
        proc = subprocess.Popen([sys.executable, str(WORKER), json.dumps(job)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=log, cwd=str(ROOT), env=env, text=True,
                                encoding='utf-8', bufsize=1, **_FLAGS)
    inbox = queue.Queue()
    threading.Thread(target=_pump, args=(proc, inbox), name='hf-fetch-pump', daemon=True).start()
    last, wait = time.monotonic(), START_S
    try:
        while True:
            try:
                msg = inbox.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - last >= wait:
                    return 'stalled', wait
                continue
            if msg is None:
                return 'error', f"the download process ended early (exit code {proc.wait()}), see user/logs/{LOG.name}"
            last, wait = time.monotonic(), stall
            if 'host' in msg:
                hosts.add(msg['host'])
            elif 'file' in msg:
                handle.name(msg['file'])
            elif 'n' in msg:
                handle.bytes(msg['n'], msg.get('total') or None)
            elif 'ok' in msg:
                return 'ok', msg['ok']
            elif 'error' in msg:
                return ('fatal' if msg.get('fatal') else 'error'), msg['error']
    finally:
        _stop(proc)


def _stalled(handle, words):
    if handle.id:
        from core import notify
        notify.tick(handle.id, phase='stalled', text=words)


def fetch(repo, files=(), patterns=(), revision=None, handle=None, stall=STALL_S):
    """Download from a Hugging Face repo. patterns: a snapshot of what matches,
    returns its folder. files: those files, returns their paths in order.
    Raises FetchError with the reason in words."""
    handle = handle or _Quiet()
    job = {'repo': repo, 'files': list(files), 'patterns': list(patterns), 'revision': revision}
    with _guard:
        lock = _locks.setdefault(repo, threading.Lock())
    with lock:
        hosts, said, routes = {HUB}, '', _routes()
        for i, (name, xet_off) in enumerate(routes):
            how, what = _run(job, xet_off, handle, stall, hosts)
            if how == 'ok':
                if i:
                    logger.info(f"[FETCH] {repo} arrived by the {name} path")
                return what
            said = f"it stopped moving for {int(what)} s" if how == 'stalled' else str(what)
            logger.warning(f"[FETCH] {repo} by the {name} path: {said}")
            if how == 'fatal':
                raise FetchError(said)
            if i + 1 < len(routes):
                _stalled(handle, ("No movement" if how == 'stalled' else "That did not work")
                         + " on the usual download path. Trying the other one.")
        _stalled(handle, "The download is not getting through. Checking the connection.")
        from core import net_check
        verdict, why = net_check.check(sorted(hosts))
        if verdict in ('ok', 'unknown'):
            why = f"The download from Hugging Face failed: {said.rstrip('.')}. {why}".strip()
        raise FetchError(why, verdict)
