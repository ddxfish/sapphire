# download.py - the download and delete jobs. Plain urllib with Range resume for single files (exact progress),
# huggingface_hub for folder snapshots, zips unpacked beside themselves. Nothing here needs the heavy env.
import hashlib
import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

from . import catalog

CHUNK = 1 << 20


def _fetch(url, dest, progress, done_before, total_all, label, retries=8):
    """Resume-able download of one file. A dropped connection resumes from where it stopped; the file is only
    accepted when every byte the host promised is here. Returns (bytes, sha256)."""
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + '.part')
    total = None
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={'User-Agent': 'sapphire-wakeword-maker'})
        if have:
            req.add_header('Range', f'bytes={have}-')
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and resp.status != 206:
                    have = 0                              # the host ignored the range: start over
                mode = 'ab' if have else 'wb'
                length = resp.headers.get('Content-Length')
                total = have + int(length) if length else total
                got = have
                last = time.monotonic()
                with open(part, mode) as fh:
                    while True:
                        buf = resp.read(CHUNK)
                        if not buf:
                            break
                        fh.write(buf)
                        got += len(buf)
                        now = time.monotonic()
                        if now - last > 0.5:
                            last = now
                            pct = 100.0 * (done_before + got) / total_all if total_all else 0
                            progress.update(pct, f"{label}: {got / 1e9:.2f} GB" + (f" of {total / 1e9:.2f}" if total else ''),
                                            bytes=done_before + got, total=total_all)
        except Exception as e:
            progress.step('download', f"{label}: connection dropped ({type(e).__name__}); resuming in a moment")
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        if total is None or part.stat().st_size >= total:
            break
        progress.step('download', f"{label}: short by {(total - part.stat().st_size) / 1e6:.0f} MB; resuming")
        time.sleep(3)
    else:
        raise RuntimeError(f"{label}: could not finish after {retries} tries")
    os.replace(part, dest)
    h = hashlib.sha256()
    with open(dest, 'rb') as fh:
        for buf in iter(lambda: fh.read(CHUNK), b''):
            h.update(buf)
    return dest.stat().st_size, h.hexdigest()


def run_download(root, args, progress):
    did = args.get('dataset')
    d = catalog.by_id(did)
    if not d:
        raise ValueError(f"no such dataset: {did}")
    ddir = catalog.dataset_dir(root, did)
    ddir.mkdir(parents=True, exist_ok=True)
    files = []
    if d.get('hf_snapshot'):
        progress.step('download', f"{d['label']}: fetching the folder")
        from huggingface_hub import snapshot_download
        snap = d['hf_snapshot']
        snapshot_download(repo_id=snap['repo'], repo_type='dataset', allow_patterns=snap.get('allow'),
                          local_dir=str(ddir))
        for base, _, names in os.walk(ddir):
            for n in names:
                if not n.endswith('.json'):
                    files.append({'name': os.path.relpath(os.path.join(base, n), ddir), 'bytes': os.path.getsize(os.path.join(base, n))})
    else:
        total_all = d.get('bytes') or 0
        done = 0
        for f in d['files']:
            progress.step('download', f"{d['label']}: {f['name']}")
            dest = ddir / f['name']
            unpacked = ddir / f['name'][:-4] if f.get('extract') and f['name'].lower().endswith('.zip') else None
            if unpacked and unpacked.is_dir() and any(unpacked.iterdir()):
                files.append({'name': f['name'], 'bytes': None, 'extracted': True, 'url': f['url'], 'note': 'already unpacked'})
                continue
            if dest.exists() and f.get('extract') and not zipfile.is_zipfile(dest):
                dest.unlink()                             # a short file from an earlier drop: fetch it again
            if dest.exists() and dest.stat().st_size > 0:
                size = dest.stat().st_size
                sha = None
            else:
                size, sha = _fetch(f['url'], dest, progress, done, total_all, f['name'])
            done += size
            entry = {'name': f['name'], 'bytes': size, 'sha256': sha, 'url': f['url']}
            if f.get('extract'):
                progress.step('unpack', f"unpacking {f['name']}")
                with zipfile.ZipFile(dest) as z:
                    z.extractall(ddir)
                dest.unlink()
                entry['extracted'] = True
            files.append(entry)
    rec = catalog.write_receipt(root, did, files)
    progress.done({'dataset': did, 'bytes': rec['bytes']})


def run_delete(root, args, progress):
    did = args.get('dataset')
    ddir = catalog.dataset_dir(root, did)
    if not catalog.by_id(did):
        raise ValueError(f"no such dataset: {did}")
    progress.step('delete', f"removing {did}")
    if ddir.is_dir():
        shutil.rmtree(ddir)
    progress.done({'dataset': did})
