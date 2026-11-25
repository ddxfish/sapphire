"""One Hugging Face download in its own process, started by core/hf_fetch.py.

A download that hangs inside the hub's native transfer code cannot be stopped
from its own thread, and it holds the cache's lock while it hangs. In a
process of its own the parent can end it and try another way.

One JSON line per message on stdout, no port: hello, host (where the bytes
come from), file (the one starting), n/total (bytes of the current bar), then
ok (the path or paths) or error. stdin closing means the parent is gone.
Whether the newer xet transfer is used is the parent's choice, made in this
process's environment (HF_HUB_DISABLE_XET) before the hub is imported.
"""
import json
import os
import sys
import threading
import time
from urllib.parse import urlparse

_out = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
os.dup2(2, 1)       # a library that prints must not write into the protocol
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # the project, not core/

XET_HOST = 'cas-server.xethub.hf.co'
FATAL = ('OfflineModeIsEnabled', 'RepositoryNotFoundError', 'RevisionNotFoundError', 'GatedRepoError',
         'RemoteEntryNotFoundError')      # the same on any path: no point trying another


def _send(**msg):
    _out.write(json.dumps(msg) + '\n')


def _exit_with_parent():
    for _ in sys.stdin:
        pass
    os._exit(0)


def _bar():
    """The tqdm class the hub is handed, or None when tqdm is not there (the
    download still runs, the row just shows no bytes)."""
    try:
        from tqdm.auto import tqdm
    except Exception:
        return None

    class Bar(tqdm):
        """Counts bytes and says them; never draws (a disabled tqdm stops counting, so it counts itself)."""

        def __init__(self, *a, **kw):
            self._bytes = kw.get('unit') in ('B', 'iB')
            self._said = 0.0
            self._lk = threading.Lock()
            kw.pop('name', None)
            kw['disable'] = True
            super().__init__(*a, **kw)

        def update(self, n=1):
            with self._lk:
                self.n = (self.n or 0) + (n or 0)
                done, total, now = self.n, self.total, time.monotonic()
                if not self._bytes or not n or (now - self._said < 0.2 and done != total):
                    return
                self._said = now
            _send(n=int(done), total=int(total or 0))

        def close(self):
            pass

    return Bar


def _say_host(job, names):
    """Where the bytes will come from, so a stall can be looked into. Never fatal."""
    probe = next((n for n in names if '*' not in n and n.endswith(('.bin', '.pth', '.onnx', '.safetensors'))), None)
    if not probe:
        return
    try:
        from huggingface_hub import constants, get_hf_file_metadata, hf_hub_url
        m = get_hf_file_metadata(hf_hub_url(job['repo'], probe, revision=job.get('revision')))
        if getattr(m, 'xet_file_data', None) is not None and not constants.HF_HUB_DISABLE_XET:
            _send(host=XET_HOST)
        elif m.location:
            _send(host=urlparse(m.location).netloc)
    except Exception:
        pass


def main():
    job = json.loads(sys.argv[1])       # {repo, files, patterns, revision}
    threading.Thread(target=_exit_with_parent, daemon=True).start()
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
        bar = _bar()
        bars = {'tqdm_class': bar} if bar else {}
        _send(hello=True)
        files, patterns, rev = job.get('files') or [], job.get('patterns') or [], job.get('revision')
        _say_host(job, files or patterns)
        if patterns:
            out = snapshot_download(job['repo'], allow_patterns=patterns, revision=rev, **bars)
        else:
            out = []
            for f in files:
                _send(file=f)
                out.append(hf_hub_download(job['repo'], f, revision=rev, **bars))
        _send(ok=out)
    except BaseException as e:
        kinds, cause = [], e
        while cause is not None and len(kinds) < 8:
            kinds.append(type(cause).__name__)
            cause = cause.__cause__ or cause.__context__
        try:
            from core.hf_words import why
            words = why(e)
        except Exception:
            words = str(e) or type(e).__name__
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {job.get('repo')}: {' <- '.join(kinds)}: {e}", file=sys.stderr)
        _send(error=words, fatal=any(k in FATAL for k in kinds) or isinstance(e, (PermissionError, MemoryError)))


if __name__ == '__main__':
    main()
