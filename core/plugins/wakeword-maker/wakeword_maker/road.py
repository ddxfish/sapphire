# road.py - the road from Settings to Install, walked by a script: every door in the order the page uses them, on a
# wake word made for the walk and taken away at the end. One list of steps, three ways to walk it:
#   pytest, hermetic      tests/test_road.py: the heavy jobs are stand-ins; seconds
#   pytest, WWM_ROAD=1    the real jobs under the plugin env in a scratch data folder, the big sets stood in small; minutes
#   the running Sapphire  `python -m wakeword_maker.cli road`: over HTTP with an API token; the sampler, the mics, the library
# A step passes, fails or is skipped, with its seconds; a failure names the door, what was sent, what came back and
# the job's log. The sizes are tiny on purpose (a 300-step net is a surrogate): the road proves the plumbing, never
# the model. Nothing it makes is worth keeping: --keep is for looking at the scratch wake word afterwards.
import asyncio
import inspect
import io
import json
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

import numpy as np

from . import paths

PLUGIN_DIR = Path(__file__).absolute().parents[1]
SR = paths.SAMPLE_RATE
TERMINAL = ('done', 'failed', 'cancelled', 'interrupted')
SCALE = {'synth': 60, 'takes': 12, 'mics': 3, 'judge': 2, 'near': 9, 'other': 6, 'pieces': 2, 'piece_s': 20,      # near: three a mic, so one a mic is held out
         'steps': 300, 'layer': 8, 'refine_trials': 2, 'refine_steps': 1000, 'mww_steps': 100, 'sat_minutes': 1}
HOWS = ('normal', 'fast', 'soft', 'loud', 'whisper', 'normal')
WHERES = ('close', 'close', 'across the room', 'next room')
NOISES = ('quiet', 'quiet', 'TV or music')
PC_MIC = 'browser:road'


class Fail(Exception):
    pass


# --- audio --------------------------------------------------------------------------------------------------

def tone(seconds=1.0, sr=SR, freq=440.0, level=0.5, lead=0.3, tail=0.3):
    t = np.arange(int(sr * seconds)) / sr
    a = (np.sin(2 * np.pi * freq * t) * level).astype(np.float32)
    return np.concatenate([np.zeros(int(sr * lead), np.float32), a, np.zeros(int(sr * tail), np.float32)])


def noise(seconds, sr=SR, level=0.01, seed=0):
    """Room-like noise: white, rolled off, with a slow swell."""
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    w = rng.standard_normal(n).astype(np.float32)
    k = np.ones(8, np.float32) / 8
    w = np.convolve(w, k, mode='same')
    swell = 1 + 0.5 * np.sin(np.arange(n) / sr * 0.7)
    return (w / (np.abs(w).max() or 1) * level * swell).astype(np.float32)


def wav_bytes(audio, sr=SR):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1, 1), sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()


def decode(data):
    import soundfile as sf
    audio, sr = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    return audio.mean(axis=1), sr


def resample(audio, sr, to):
    if sr == to:
        return audio
    n = int(round(len(audio) * to / sr))
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def mix(seconds, inserts=(), seed=0, level=0.01):
    """`seconds` of room with clips laid in at (at_seconds, audio)."""
    out = noise(seconds, level=level, seed=seed)
    for at, clip in inserts:
        a = int(at * SR)
        b = min(len(out), a + len(clip))
        out[a:b] += clip[:b - a]
    return np.clip(out, -1, 1)


# --- the report --------------------------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.rows = []

    def add(self, step, state, seconds, detail='', error=''):
        self.rows.append({'step': step, 'state': state, 'seconds': round(seconds, 1), 'detail': detail, 'error': error})

    @property
    def failed(self):
        return [r for r in self.rows if r['state'] == 'fail']

    def table(self):
        w = max((len(r['step']) for r in self.rows), default=8)
        lines = [f"  {r['state'].upper():5} {r['step']:{w}}  {r['seconds']:7.1f}s  {r['detail']}" for r in self.rows]
        for r in self.failed:
            lines.append(f"\n{r['step']} failed:\n{r['error']}")
        n = len(self.rows)
        lines.append(f"\n{n - len(self.failed) - len([r for r in self.rows if r['state'] == 'skip'])} passed, {len(self.failed)} failed, "
                     f"{len([r for r in self.rows if r['state'] == 'skip'])} skipped · {sum(r['seconds'] for r in self.rows):.0f}s")
        return '\n'.join(lines)


# --- the doors ---------------------------------------------------------------------------------------------

class _Upload:
    def __init__(self, name, data):
        self.filename, self._data = name, data

    async def read(self):
        return self._data


class _Form:
    def __init__(self, fields, files):
        self._fields, self._files = fields, files

    def get(self, k, default=None):
        if k in self._fields:
            return self._fields[k]
        return next((v for kk, v in self._files if kk == k), default)

    def multi_items(self):
        return list(self._fields.items()) + self._files


class _Request:
    def __init__(self, fields=None, files=None, body=None):
        self._form = _Form(fields or {}, [(k, _Upload(n, d)) for k, n, d in (files or [])])
        self._body = body
        self.headers = {'content-type': 'application/json' if body is not None else 'multipart/form-data'}

    async def form(self):
        return self._form

    async def json(self):
        return self._body


class Local:
    """The handlers in this process, found through the manifest's route table, like the loader finds them."""

    def __init__(self, root, settings=None, api=None):
        self.root = Path(root)
        self.settings = settings or {'data_dir': str(root), 'device': 'cpu', 'qa_whisper_model': 'base.en'}
        if api is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location('wwm_routes_api_road', PLUGIN_DIR / 'routes' / 'api.py')
            api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(api)
        self.api = api
        routes = json.loads((PLUGIN_DIR / 'plugin.json').read_text(encoding='utf-8'))['capabilities']['routes']
        self.table = []
        for r in routes:
            pat = re.compile('^' + re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', r['path']) + '$')
            self.table.append((r['method'].upper(), pat, r['handler'].rsplit(':', 1)[1]))

    def call(self, method, path, body=None, query=None, files=None, fields=None, timeout=None):
        for m, pat, name in self.table:
            mo = pat.match(path)
            if m == method and mo:
                break
        else:
            return 404, {'error': f"no door {method} {path}"}
        fn = getattr(self.api, name)
        req = _Request(fields, files) if (files or fields) else _Request(body=body if body is not None else {})
        kw = {**mo.groupdict(), 'body': body or {}, 'settings': self.settings, 'query': query or {}, 'request': req, 'credentials': {}}
        out = fn(**kw)
        if inspect.iscoroutine(out):
            out = asyncio.run(out)
        if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], int):
            return out[1], out[0]
        if hasattr(out, 'status_code'):                       # a Response: a file or plain text
            p = getattr(out, 'path', None)
            return out.status_code, (Path(p).read_bytes() if p else getattr(out, 'body', b''))
        return 200, out

    def core(self, method, path, body=None):
        """A core door, in process: only the library's remove is needed on the road."""
        mo = re.match(r'^/api/wakeword/library/(\w+)/([\w-]+)$', path)
        if method == 'DELETE' and mo:
            from core.wakeword import library
            return 200, {'removed': mo.group(2), 'used_by': library.remove(mo.group(2), mo.group(1))}
        return 404, {'error': f"no core door {method} {path} in process"}


class Live:
    """The running Sapphire over HTTP with a named API token (Settings > System > API keys), paced under core's
    limits (30 writes and 60 reads a minute per token)."""

    def __init__(self, base, token, verify=False):
        import requests
        self.base, self.token, self.verify, self.rq = base.rstrip('/'), token, verify, requests
        self.recent = {'w': deque(), 'r': deque()}
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    def _gate(self, method):
        kind, limit = ('r', 56) if method == 'GET' else ('w', 27)
        q = self.recent[kind]
        now = time.time()
        while q and now - q[0] > 61:
            q.popleft()
        if len(q) >= limit:
            time.sleep(max(0.0, 61 - (now - q[0])))
        q.append(time.time())

    def _send(self, method, url, body, query, files, fields, timeout):
        self._gate(method)
        kw = {'headers': {'Authorization': f"Bearer {self.token}"}, 'params': query or None, 'verify': self.verify, 'timeout': timeout or 120}
        if files or fields:
            kw['files'] = [(k, (n, d, 'audio/wav')) for k, n, d in (files or [])]
            kw['data'] = fields or {}
        elif body is not None or method in ('POST', 'PUT', 'DELETE'):
            kw['json'] = body or {}
        r = self.rq.request(method, url, **kw)
        if 'json' in (r.headers.get('content-type') or ''):
            try:
                return r.status_code, r.json()
            except Exception:
                pass
        return r.status_code, r.content

    def call(self, method, path, body=None, query=None, files=None, fields=None, timeout=None):
        return self._send(method, f"{self.base}/api/plugin/wakeword-maker/{path}", body, query, files, fields, timeout)

    def core(self, method, path, body=None):
        return self._send(method, f"{self.base}{path}", body, None, None, None, 60)


# --- the walk ------------------------------------------------------------------------------------------------

class Road:
    def __init__(self, door, phrase='hey road test', scale=None, esp32=False, install=True, mic=True, satellites=True,
                 keep=False, sampler='live', slow=1.0, say=None):
        self.door, self.phrase = door, ' '.join(str(phrase).split())
        self.slug = paths.slug_of(self.phrase)
        self.scale = {**SCALE, **(scale or {})}
        self.esp32, self.install, self.mic, self.satellites, self.keep = esp32, install, mic, satellites, keep
        self.sampler, self.slow = sampler, float(slow)
        self.say = say or (lambda s: None)
        self.report = Report()
        self.jobs, self.watches, self.grids = [], [], []
        self.rid = self.rid_mww = None
        self.poll = 0.3 if isinstance(door, Local) else 2.0

    # -- doors --
    def call(self, method, path, body=None, query=None, files=None, fields=None, timeout=None, ok=(200,)):
        code, data = self.door.call(method, path, body=body, query=query, files=files, fields=fields, timeout=timeout)
        if code not in ok:
            err = data.get('error') or data.get('detail') if isinstance(data, dict) else (data[:200] if isinstance(data, (bytes, str)) else data)
            sent = body if body is not None else (fields or query or '')
            raise Fail(f"{method} {path} answered {code}: {err}\n  sent: {json.dumps(sent, default=str)[:300]}")
        return data

    def get(self, path, query=None, **kw):
        return self.call('GET', path, query=query, **kw)

    def post(self, path, body=None, **kw):
        return self.call('POST', path, body=body, **kw)

    def put(self, path, body=None, **kw):
        return self.call('PUT', path, body=body, **kw)

    def delete(self, path, query=None, body=None, **kw):
        return self.call('DELETE', path, query=query, body=body, **kw)

    def job(self, kind, args, timeout):
        j = self.post('jobs', {'kind': kind, 'project': self.slug, 'args': args})['job']
        self.jobs.append(j['id'])
        return self.wait(j['id'], timeout)

    def wait(self, jid, timeout):
        end = time.time() + timeout * self.slow
        last = ''
        while True:
            j = self.get(f"jobs/{jid}")['job']
            if j['state'] in TERMINAL:
                break
            if j.get('msg') and j['msg'] != last:
                last = j['msg']
                self.say(f"      {j['kind']} · {j.get('pct', 0):.0f}% · {last[:90]}")
            if time.time() > end:
                raise Fail(f"job {jid} ({j['kind']}) still {j['state']} after {timeout * self.slow:.0f}s: {j.get('msg')}")
            time.sleep(self.poll)
        if j['state'] != 'done':
            raise Fail(f"job {jid} ({j['kind']}) {j['state']}: {j.get('error') or j.get('msg')}\n--- log tail ---\n{self.log(jid)}")
        return j

    def log(self, jid):
        try:
            data = self.get(f"jobs/{jid}/log")
            text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else str(data)
            return '\n'.join(text.strip().splitlines()[-25:])
        except Exception as e:
            return f"(no log: {e})"

    # -- what the road already made (so --only can start anywhere) --
    def run_id(self, trainer='oww'):
        have = self.rid if trainer == 'oww' else self.rid_mww
        if have:
            return have
        picks = self.get(f"projects/{self.slug}/install").get('picks') or {}
        found = (picks.get(trainer) or {}).get('id') or next((r['id'] for r in self.get(f"projects/{self.slug}/runs")['runs']
                                                           if r.get('trainer') == trainer and r.get('state') == 'done' and r.get('has_model') and r.get('kind') != 'tune'), None)
        if not found:
            raise Fail(f"no finished {trainer} run on {self.slug} yet: walk the train step first")
        if trainer == 'oww':
            self.rid = found
        else:
            self.rid_mww = found
        return found

    def clips(self, collection, n):
        rows = self.get(f"projects/{self.slug}/clips", {'collection': collection, 'limit': n})['clips']
        if len(rows) < n:
            raise Fail(f"{collection} holds {len(rows)} clips, {n} needed: walk the synthetic step first")
        out = []
        for r in rows[:n]:
            data = self.get(f"projects/{self.slug}/clips/{collection}/{r['id']}/audio")
            audio, sr = decode(data)
            out.append((r, resample(audio, sr, SR)))
        return out

    # -- the steps --
    def settings(self):
        st = self.get('status')
        env = st.get('env') or {}
        if env.get('state') not in ('ready', 'stale'):
            raise Fail(f"the plugin's environment is {env.get('state')}: build it on Settings first")
        ds = {d['id']: d for d in self.get('datasets')['datasets']}
        need = ['oww_models', 'oww_negatives', 'oww_validation'] + (['mww_negatives'] if self.esp32 else [])
        missing = [d for d in need if ds.get(d, {}).get('state') != 'ready']
        if missing:
            raise Fail(f"download on Settings first: {', '.join(missing)}")
        extras = [d for d in ('rirs', 'music') if ds.get(d, {}).get('state') != 'ready']
        self.get('storage')
        self.get('train/options')
        self.get('voices')
        self.get('variations/presets')
        gpu = (st.get('gpu') or {}).get('name') or 'no GPU'
        return f"env {env['state']} · {gpu} · sets ready" + (f" (without {', '.join(extras)})" if extras else '')

    def wakeword(self):
        if any(p['slug'] == self.slug for p in self.get('projects')['projects']):
            pr = self.get(f"projects/{self.slug}")['project']
            if not (pr.get('settings') or {}).get('road'):
                raise Fail(f"a wake word '{self.phrase}' is already here and is not the road's: pick another --phrase")
            self.delete(f"projects/{self.slug}")
        pr = self.post('projects', {'phrase': self.phrase})['project']
        if pr['slug'] != self.slug:
            raise Fail(f"slug {pr['slug']} for '{self.phrase}', expected {self.slug}")
        cat = self.get('voices')
        on_disk = [p for p in cat['piper'] if p.get('on_disk')]
        piper = (max(on_disk, key=lambda p: p['speakers'])['name'] if on_disk else 'en_US-amy-medium')
        kok = ['af_heart'] if cat['kokoro'].get('model_on_disk') and 'af_heart' in (cat['kokoro'].get('on_disk') or []) else []
        words = self.phrase.split()
        near = [' '.join(['a'] + words[1:]), ' '.join(words[1:])] if len(words) > 1 else [f"say {self.phrase}"]
        synth = {'preset': 'quick', 'target': self.scale['synth'], 'piper': [piper], 'kokoro': kok, 'kokoro_other': False, 'blends': False,
                 'variation': 'normal', 'share_kokoro': 0.25 if kok else 0.0}
        pr = self.put(f"projects/{self.slug}", {'negatives_auto': near, 'settings': {'road': True, 'synth': synth, 'qa_bar': 60}})['project']
        pg = pr.get('progress') or {}
        if pg.get('of') != 7 or pg.get('step') != 'voices':
            raise Fail(f"progress reads {pg}")
        return f"{self.slug} · voice {piper}" + (f" + {kok[0]}" if kok else '') + ('' if on_disk else ' (downloads once)')

    def synthetic(self):
        j = self.job('synth', {}, 900)
        res = j.get('result') or {}
        counts = res.get('counts') or {}
        pos, neg = counts.get('positive/synth', 0), counts.get('negative/synth', 0)
        if pos < self.scale['synth'] * 0.8 or neg < 1:
            raise Fail(f"the set came out {pos} positives, {neg} sound-alikes: {res}")
        rows = self.get(f"projects/{self.slug}/clips", {'collection': 'positive/synth', 'limit': 3})['clips']
        if not rows or not rows[0].get('voice'):
            raise Fail(f"synthetic clips carry no voice: {rows[:1]}")
        extra = ''
        if self.sampler != 'skip':
            pr = self.get(f"projects/{self.slug}")['project']
            voice = (pr.get('settings') or {}).get('synth', {}).get('piper', ['en_US-amy-medium'])[0]
            data = self.get('voices/sample', {'engine': 'piper', 'voice': voice, 'text': self.phrase}, timeout=180)
            if not isinstance(data, bytes) or len(data) < 1000:
                raise Fail(f"the voice sampler answered {type(data).__name__} of {len(data) if data else 0} bytes")
            extra = ' · sampler spoke'
        return f"{pos} positives, {neg} sound-alikes, {res.get('failed', 0)} failed in {res.get('seconds', '?')}s{extra}"

    def human(self):
        sc = self.scale
        takes = self.clips('positive/synth', sc['takes'] * sc['mics'])
        negs = self.clips('negative/synth', sc['near'] + sc['other'])
        kept = 0
        for m in range(sc['mics']):
            device = f"road-mic-{m + 1}"
            mine = takes[m * sc['takes']:(m + 1) * sc['takes']]
            batches = [mine[:sc['judge']]] + [mine[i:i + 4] for i in range(sc['judge'], len(mine), 4)]
            for b, batch in enumerate(batches):
                i = m * 7 + b
                fields = {'collection': 'positive/recorded', 'device': device, 'how': HOWS[i % len(HOWS)], 'where': WHERES[i % len(WHERES)],
                          'noise': NOISES[i % len(NOISES)], 'who': 'Sam' if (m + b) % 3 == 2 else '', 'trim': '1'}
                if b == 0:
                    fields['judge'] = '1'
                files = [('audio', f"take{k}.wav", wav_bytes(resample(a, SR, 48000), 48000)) for k, (_, a) in enumerate(batch)]
                r = self.post(f"projects/{self.slug}/record", fields=fields, files=files)
                if len(r.get('clips') or []) != len(batch):
                    raise Fail(f"{device}: sent {len(batch)} takes, kept {len(r.get('clips') or [])}: {r.get('refused')}")
                kept += len(batch)
        groups = {}
        for k, (row, a) in enumerate(negs):                       # batched like the page: one post per mic and kind (core meters writes)
            groups.setdefault((f"road-mic-{k % sc['mics'] + 1}", k < sc['near']), []).append((row.get('text') or 'other words', a))
        for (device, near), items in groups.items():
            fields = {'collection': 'negative/recorded', 'device': device, 'texts': '\n'.join(t for t, _ in items), **({'near': '1'} if near else {})}
            r = self.post(f"projects/{self.slug}/record", fields=fields, files=[('audio', f"n{k}.wav", wav_bytes(a)) for k, (_, a) in enumerate(items)])
            if len(r.get('clips') or []) != len(items):
                raise Fail(f"{device}: sent {len(items)} negatives, kept {len(r.get('clips') or [])}: {r.get('refused')}")
        # the level check, the way the page does it before a session
        levels = []
        if self.mic and isinstance(self.door, Live):
            try:
                import sounddevice as sd
                secs = 3
                rec = sd.rec(int(secs * SR), samplerate=SR, channels=1, dtype='float32')
                sd.wait()
                rep = self.post(f"projects/{self.slug}/record-test", fields={}, files=[('audio', 'mic.wav', wav_bytes(rec.reshape(-1)))])
                levels.append(f"PC mic {rep.get('verdict')} (floor {rep.get('floor_db')} dB, peak {rep.get('peak_db')} dB)")
            except ImportError:
                levels.append('PC mic not grabbed (sounddevice is not in this interpreter)')
            except Exception as e:
                levels.append(f"PC mic: {e}")
            for mic in self.get('mics')['mics'] if self.satellites else []:
                if not mic.get('online'):
                    continue
                try:
                    rep = self.post(f"projects/{self.slug}/record-test", {'device': mic['id']}, timeout=60)
                    levels.append(f"{mic['id']} {rep.get('verdict')} (floor {rep.get('floor_db')} dB)")
                except Fail as e:
                    levels.append(f"{mic['id']}: {str(e).splitlines()[0]}")
        else:
            rep = self.post(f"projects/{self.slug}/record-test", fields={}, files=[('audio', 'mic.wav', wav_bytes(tone()))])
            if rep.get('verdict') not in ('good', 'quiet', 'noisy', 'bad', 'clipped', 'short'):
                raise Fail(f"record-test answered {rep}")
            levels.append(f"a tone reads {rep['verdict']}")
        g = self.get(f"projects/{self.slug}/guide")
        have = (g.get('progress') or {}).get('positive', {}).get('have')
        if have != kept:
            raise Fail(f"the guide counts {have} takes, {kept} were recorded")
        if not self.get(f"projects/{self.slug}/near-misses").get('phrases'):
            raise Fail('no near-miss prompts')
        self.get(f"projects/{self.slug}/browse", {'collection': 'positive/recorded', 'limit': 5})
        rows = self.get(f"projects/{self.slug}/clips", {'collection': 'positive/recorded', 'limit': 1})['clips']
        r = self.put(f"projects/{self.slug}/clips/positive/recorded/{rows[0]['id']}", {'verdict': 'keep'})
        if r['clip'].get('verdict_by') != 'user':
            raise Fail(f"a verdict by hand was not kept as the user's: {r['clip']}")
        judged = sum(1 for c in (g.get('checks') or []) if 'for the judge' in c.get('text', ''))
        return f"{kept} takes on {sc['mics']} mics, {sc['near']} near misses, {sc['other']} other words" + (' · judge takes seen' if judged else '') + ' · ' + '; '.join(levels)

    def background(self):
        sc = self.scale
        notes = []
        if self.satellites and isinstance(self.door, Live):
            sats = [m for m in self.get('mics')['mics'] if m.get('online')]
            if sats:
                r = self.post(f"projects/{self.slug}/room-day", {'minutes': sc['sat_minutes'], 'devices': [m['id'] for m in sats], 'test': False}, timeout=60)
                ids = [s['id'] for s in r.get('started') or []]
                self.watches += ids
                for x in r.get('refused') or []:
                    notes.append(f"{x['device']} refused: {x['error']}")
                end = time.time() + sc['sat_minutes'] * 60 + 150
                while ids and time.time() < end:
                    ws = {w['id']: w for w in self.get(f"projects/{self.slug}/watch")['watches']}
                    if all(ws.get(i, {}).get('ended') for i in ids):
                        break
                    time.sleep(5)
                else:
                    if ids:
                        self.delete(f"projects/{self.slug}/room-day")
                        notes.append('satellite day did not end on its own; stopped')
                lib = {w['watch']: w for w in self.get('room')['watches']}
                for i in ids:
                    w = lib.get(i)
                    notes.append(f"{w['device']}: {w['pieces']} piece(s), {w['minutes']} min" if w else f"watch {i} left no piece in the library")
                    if not w:
                        raise Fail('; '.join(notes))
            else:
                notes.append('no satellite online')
        # a PC microphone's day: the page posts pieces; here the pieces are made
        w = self.post(f"projects/{self.slug}/watch", {'device': PC_MIC, 'minutes': 5, 'runs': '', 'keep': True})['watch']
        self.watches.append(w['id'])
        for k in range(sc['pieces']):
            st = self.post(f"projects/{self.slug}/watch/chunk", fields={'device': PC_MIC}, files=[('audio', 'p.wav', wav_bytes(noise(sc['piece_s'], seed=k)))], timeout=300)['watch']
        if st.get('kept') != sc['pieces']:
            raise Fail(f"{sc['pieces']} pieces posted, the watch kept {st.get('kept')}: {st.get('errors')}")
        st = self.delete(f"projects/{self.slug}/watch", {'device': PC_MIC})['watch']
        if not st.get('ended'):
            raise Fail(f"the PC watch did not end: {st}")
        lib = {x['watch']: x for x in self.get('room', {'project': self.slug})['watches']}
        if lib.get(w['id'], {}).get('pieces') != sc['pieces']:
            raise Fail(f"the library holds {lib.get(w['id'])} for the PC watch")
        self.post(f"projects/{self.slug}/record", fields={'collection': 'ambient/recorded', 'device': 'road-mic-1', 'trim': '0'}, files=[('audio', 'room.wav', wav_bytes(noise(5, seed=9)))])
        up = self.post(f"projects/{self.slug}/upload", fields={'collection': 'ambient/uploaded'}, files=[('files', 'room2.wav', wav_bytes(noise(5, seed=10)))])
        if up.get('added') != 1:
            raise Fail(f"upload: {up}")
        pg = self.get(f"projects/{self.slug}")['project']['progress']
        want = sc['pieces'] * sc['piece_s'] / 60 + 10 / 60
        if not pg.get('room') or pg['room'] < want * 0.9:
            raise Fail(f"progress counts {pg.get('room')} min of room, {want:.1f} expected")
        return f"PC day {sc['pieces']}×{sc['piece_s']}s kept · one take + one upload · {pg['room']} min of room" + (' · ' + '; '.join(notes) if notes else '')

    def check(self):
        j = self.job('qa', {'bar': 60}, 900)
        rated = (j.get('result') or {}).get('rated', 0)
        rep = self.get(f"projects/{self.slug}/check")
        c = rep['counts']
        if c.get('rated', 0) < rated or rated < self.scale['synth']:
            raise Fail(f"rated {rated}; the report counts {c}")
        if not self.get(f"projects/{self.slug}/qa").get('summary'):
            raise Fail('no QA summary after rating')
        dropped = self.get(f"projects/{self.slug}/dropped")
        return f"{rated} rated, {c.get('dropped', 0)} dropped ({dropped.get('total')} by reason) · {len(rep.get('advice') or [])} advice line(s)"

    def train(self):
        sc = self.scale
        cfg = {'steps': sc['steps'], 'layer_size': sc['layer'], 'n_blocks': 1}
        j = self.job('train', {'preset': 'quick', 'recipe': 'natural', 'rounds': 1, 'holdout': True, 'cfg': cfg, 'label': 'road single'}, 1500)
        res = j.get('result') or {}
        rid = res.get('run')
        if not rid:
            raise Fail(f"the train job left no run: {res}")
        self.rid = rid
        runs = {r['id']: r for r in self.get(f"projects/{self.slug}/runs")['runs']}
        r = runs.get(rid) or {}
        if r.get('state') != 'done' or not r.get('has_model'):
            raise Fail(f"run {rid} reads {r.get('state')}, model {r.get('has_model')}")
        v = r.get('heldout') or {}
        if v.get('recall') is None:
            raise Fail(f"the run was not judged on the held-out slice: {v}")
        one = self.get(f"projects/{self.slug}/runs/{rid}")
        if not one.get('metrics'):
            raise Fail('no metrics lines for the run')
        jr = self.get(f"projects/{self.slug}/runs/{rid}/judge")
        if not jr.get('rows'):
            raise Fail(f"the judge's rows are empty: {jr}")
        model = self.get(f"projects/{self.slug}/runs/{rid}/file/model.onnx")
        if not isinstance(model, bytes) or len(model) < 4096:
            raise Fail(f"model.onnx came back as {type(model).__name__} of {len(model) if model else 0} bytes (the library wants 4 KB or more)")
        self.put(f"projects/{self.slug}/runs/{rid}", {'chosen': True, 'label': 'road single'})
        h = self.get(f"projects/{self.slug}/holdout")
        picks = self.get(f"projects/{self.slug}/install").get('picks') or {}
        if (picks.get('oww') or {}).get('id') != rid:
            raise Fail(f"Install does not pick the ★ run: {picks}")
        return (f"{rid} · {r.get('seconds', '?')}s · hears {100 * v['recall']:.0f}% of {v.get('n_pos')} held-out, "
                f"{v.get('near_fires')} of {v.get('n_near')} sound-alikes fire, threshold {v.get('threshold')} · model {len(model) // 1024} KB")

    def search(self):
        sc = self.scale
        body = {'trainers': ['oww'] + (['mww'] if self.esp32 else []), 'recipes': ['natural'], 'sources': ['all'], 'presets': ['quick'],
                'knobs': {'oww': ['default'], 'mww': ['default']}, 'seeds': 1, 'rounds': 1, 'label': 'road',
                'cfg': {'steps': sc['steps'], 'layer_size': sc['layer'], 'n_blocks': 1, 'eval_every': max(10, sc['mww_steps'] // 2)},
                'refine': {'trials': sc['refine_trials'], 'budget_steps': sc['refine_steps'], 'parallel': 1, 'top': 1}}
        if self.esp32:
            body['cfg']['steps'] = sc['steps']          # oww and mww take the same key; the ESP32 step sets its own
        g = self.post(f"projects/{self.slug}/grid", body)['grid']
        self.grids.append(g['id'])
        want = 2 if self.esp32 else 1
        if g.get('of') != want or not g.get('refine', {}).get('job'):
            raise Fail(f"the grid queued {g.get('of')} cells (wanted {want}), refine {g.get('refine')}")
        for c in g['cells']:
            self.jobs.append(c['job'])
            self.wait(c['job'], 1500)
        self.jobs.append(g['refine']['job'])
        self.wait(g['refine']['job'], 2400)
        g = self.get(f"projects/{self.slug}/grids/{g['id']}")['grid']
        bad = [c for c in g['cells'] if c.get('state') != 'done' or c.get('recall') is None]
        if bad:
            raise Fail(f"cells not done or unjudged: {bad}")
        rf = g.get('refine') or {}
        if rf.get('state') != 'done' or not rf.get('run'):
            raise Fail(f"the refine reads {rf}")
        if not any(x['id'] == g['id'] for x in self.get(f"projects/{self.slug}/grids")['grids']):
            raise Fail('the grid is not listed')
        tune = self.get(f"projects/{self.slug}/runs/{rf['run']}")
        trials = tune.get('trials') or []
        if len(trials) < sc['refine_trials'] or not any(t.get('judged') for t in trials):
            raise Fail(f"the refine logged {len(trials)} trials, judged {[t.get('judged') for t in trials]}")
        v = (tune.get('run') or {}).get('heldout') or {}
        return (f"grid {g['id']}: {len(g['cells'])} cell(s) judged · refine {rf['run']}: {len(trials)} trials, best {tune['run'].get('best_params')}, "
                f"final hears {100 * (v.get('recall') or 0):.0f}%")

    def esp32(self):
        if not self.esp32:
            return None
        sc = self.scale
        j = self.job('train_mww', {'preset': 'quick', 'recipe': 'natural', 'rounds': 1, 'holdout': True, 'label': 'road esp32',
                                   'cfg': {'steps': sc['mww_steps'], 'eval_every': max(10, sc['mww_steps'] // 2)}}, 3000)
        rid = (j.get('result') or {}).get('run')
        if not rid:
            raise Fail(f"the ESP32 job left no run: {j.get('result')}")
        self.rid_mww = rid
        man = self.get(f"projects/{self.slug}/runs/{rid}/file/model.json")
        man = json.loads(man.decode('utf-8')) if isinstance(man, bytes) else man
        if not (man.get('micro') or {}).get('probability_cutoff'):
            raise Fail(f"the manifest reads {man}")
        self.put(f"projects/{self.slug}/runs/{rid}", {'chosen': True})
        v = (j.get('result') or {}).get('heldout') or {}
        return f"{rid} · cutoff {man['micro']['probability_cutoff']} · hears {100 * (v.get('recall') or 0):.0f}%"

    def test(self):
        rid = self.run_id()
        if self.sampler == 'skip':
            j = self.job('judge', {'force': True}, 900)
            return f"re-judged {(j.get('result') or {}).get('judged')} run(s) · Try it, the dry run and the fires watch need the sampler: --live"
        word = self.get(f"projects/{self.slug}/dryrun", {'n': 40}, timeout=600).get('verdict')
        var = self.get(f"projects/{self.slug}/variation", {'clip': 'random'}, timeout=120)
        if not isinstance(var, bytes) or len(var) < 1000:
            raise Fail('the variation door gave no audio')
        (prow, phrase), = self.clips('positive/synth', 1)
        (nrow, near), = self.clips('negative/synth', 1)
        hit = self.post(f"projects/{self.slug}/try", fields={'runs': rid, 'key': '0'}, files=[('audio', 'say.wav', wav_bytes(phrase))], timeout=300)
        miss = self.post(f"projects/{self.slug}/try", fields={'runs': rid, 'key': '0'}, files=[('audio', 'near.wav', wav_bytes(near))], timeout=300)
        a, b = (hit.get('runs') or {}).get(rid) or {}, (miss.get('runs') or {}).get(rid) or {}
        if a.get('score') is None or b.get('score') is None or not hit.get('mic'):
            raise Fail(f"Try it answered {hit} / {miss}")
        # the room watch with the model on it: a piece with the phrase in it, the fire sorted
        w = self.post(f"projects/{self.slug}/watch", {'device': PC_MIC, 'minutes': 5, 'runs': rid, 'keep': False})['watch']
        st = self.post(f"projects/{self.slug}/watch/chunk", fields={'device': PC_MIC},
                       files=[('audio', 'p.wav', wav_bytes(mix(20, [(6.0, phrase), (14.0, near)], seed=3)))], timeout=600)['watch']
        fires = st.get('fires') or []
        sorted_ = []
        for f, what in zip(fires, ('positive', 'negative')):
            r = self.post(f"projects/{self.slug}/watch/{w['id']}/{f['id']}", {'as': what})
            sorted_.append(r['fire'].get('fate'))
        st = self.delete(f"projects/{self.slug}/watch", {'device': PC_MIC})['watch']
        if not st.get('ended'):
            raise Fail(f"the fires watch did not end: {st}")
        if st.get('errors'):
            raise Fail(f"the watch reports errors: {st['errors']}")
        j = self.job('judge', {'force': True}, 900)
        rows = self.get(f"projects/{self.slug}/runs/{rid}/judge").get('rows') or []
        return (f"recipe dry run: {word} · Try it: phrase {a['score']:.2f} vs sound-alike {b['score']:.2f} at {a.get('threshold')}"
                f" · watch: {len(fires)} fire(s){', sorted ' + '/'.join(sorted_) if sorted_ else ''} · re-judged {(j.get('result') or {}).get('judged')}, {len(rows)} judge rows")

    def install_step(self):
        rid = self.run_id()
        before = self.get(f"projects/{self.slug}/install")
        sats = before.get('satellites') or []
        if not self.install:
            return f"skipped by --no-install · {len(sats)} satellite(s) listed, none sent"
        r = self.post(f"projects/{self.slug}/install/desktop", {})
        if not (r.get('desktop') or {}).get('ours') or r.get('run') != rid:
            raise Fail(f"after the install this computer reads {r}")
        pg = self.get(f"projects/{self.slug}")['project']['progress']
        if not pg['done'].get('install'):
            raise Fail(f"the install step is not marked done: {pg}")
        code, back = self.door.call('DELETE', f"projects/{self.slug}/install/desktop")
        if code == 200:
            put = f"put back {before['desktop'].get('model') or 'nothing'}"
        elif code == 400 and 'nothing to put back' in str(back):
            put = 'nothing was listening before'
        else:
            raise Fail(f"put back answered {code}: {back}")
        code, rm = self.door.core('DELETE', f"/api/wakeword/library/onnx/{self.slug}")
        if code != 200:
            raise Fail(f"the library would not let go of {self.slug}: {code} {rm}")
        return f"installed {rid} at {r.get('threshold')}, core loads ours · {put} · out of the library · {len(sats)} satellite(s) listed, none sent"

    def done(self):
        pr = self.put(f"projects/{self.slug}", {'steps': {k: True for k in ('voices', 'record', 'background', 'check', 'test', 'install')}})['project']
        pg = pr['progress']
        if pg.get('next') != 'All done' or pg.get('pct') != 100 or not all(pg['done'].values()):
            raise Fail(f"progress reads {pg}")
        if not any(p['slug'] == self.slug for p in (self.get('storage').get('projects') or [])):
            raise Fail('storage does not list the wake word')
        return 'all seven steps done, 100%'

    def cleanup(self):
        if self.keep:
            return f"kept: {self.slug}, {len(self.watches)} room watch(es), {len(self.jobs)} job(s)"
        gone = []
        for gid in self.grids:
            self.door.call('DELETE', f"projects/{self.slug}/grids/{gid}")
        for wid in self.watches:
            code, r = self.door.call('DELETE', 'room', query={'watch': wid})
            gone.append(f"{wid}:{(r or {}).get('deleted') if isinstance(r, dict) else code}")
        self.delete(f"projects/{self.slug}")
        if any(p['slug'] == self.slug for p in self.get('projects')['projects']):
            raise Fail('the wake word is still listed after delete')
        forgot = 0
        for j in self.get('jobs', {'project': self.slug, 'limit': 200})['jobs']:
            if j['state'] in TERMINAL:
                code, r = self.door.call('DELETE', f"jobs/{j['id']}")
                forgot += int(bool(isinstance(r, dict) and r.get('forgotten')))
        return f"{self.slug} deleted · room pieces {', '.join(gone) or 'none'} · {forgot} job(s) forgotten"

    STEPS = (('settings', 'settings'), ('wakeword', 'wakeword'), ('synthetic', 'synthetic'), ('human', 'human'), ('background', 'background'),
             ('check', 'check'), ('train', 'train'), ('search', 'search'), ('esp32', 'esp32'), ('test', 'test'), ('install', 'install_step'),
             ('done', 'done'), ('cleanup', 'cleanup'))

    def walk(self, only=None):
        """Every step in order; after a failure the rest are skipped and the cleanup still runs. `only`: these steps."""
        broken = False
        for name, fn in self.STEPS:
            if only and name not in only:
                continue
            if name == 'esp32' and not self.esp32:
                self.report.add(name, 'skip', 0, 'not asked for (--esp32)')
                continue
            if broken and name != 'cleanup':
                self.report.add(name, 'skip', 0, 'after a failure')
                continue
            self.say(f"  {name} ...")
            t0 = time.time()
            try:
                detail = getattr(self, fn)()
                self.report.add(name, 'pass', time.time() - t0, detail or '')
                self.say(f"  [PASS] {name} {time.time() - t0:.1f}s  {detail or ''}")
            except Fail as e:
                self.report.add(name, 'fail', time.time() - t0, '', str(e))
                self.say(f"  [FAIL] {name} {time.time() - t0:.1f}s\n{e}")
                broken = True
            except Exception as e:
                import traceback
                self.report.add(name, 'fail', time.time() - t0, '', f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}")
                self.say(f"  [FAIL] {name}: {type(e).__name__}: {e}")
                broken = True
        return self.report


# --- stand-ins for a scratch data folder ------------------------------------------------------------------------

def standins(root, real_root=None, esp32=False, python=None, rows=4000, val_rows=3000):
    """A scratch data folder's big sets, small, so the real trainers run in seconds: slices of the real sets when a
    real data folder has them, random numbers of the right shape otherwise; the two feature models and the voices
    are linked from the real folder. Returns what could not be stood in."""
    from . import catalog
    root, real = Path(root), Path(real_root or paths.DEFAULT_ROOT)
    missing = []
    d = catalog.dataset_dir(root, 'oww_models')
    src = catalog.dataset_dir(real, 'oww_models')
    names = ('melspectrogram.onnx', 'embedding_model.onnx')
    if all((src / n).is_file() for n in names):
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            shutil.copy(src / n, d / n)
        catalog.write_receipt(root, 'oww_models', list(names))
    else:
        missing.append('oww_models')
    rng = np.random.default_rng(1)
    big = catalog.dataset_dir(real, 'oww_negatives') / 'openwakeword_features_ACAV100M_2000_hrs_16bit.npy'
    d = catalog.dataset_dir(root, 'oww_negatives')
    d.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(np.load(big, mmap_mode='r')[:rows]) if big.is_file() else rng.standard_normal((rows, 16, 96)).astype(np.float16)
    np.save(d / big.name, arr)
    catalog.write_receipt(root, 'oww_negatives', [big.name])
    val = catalog.dataset_dir(real, 'oww_validation') / 'validation_set_features.npy'
    d = catalog.dataset_dir(root, 'oww_validation')
    d.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(np.load(val, mmap_mode='r')[:val_rows]) if val.is_file() else rng.standard_normal((val_rows, 96)).astype(np.float32)
    np.save(d / val.name, arr)
    catalog.write_receipt(root, 'oww_validation', [val.name])
    vd, rv = paths.voices_dir(root), paths.voices_dir(real)
    vd.mkdir(parents=True, exist_ok=True)
    for p in rv.glob('*.onnx') if rv.is_dir() else []:
        for f in (p, p.with_name(p.name + '.json')):
            if f.is_file() and not (vd / f.name).exists():
                os.symlink(f, vd / f.name)
    if (rv / 'hf').is_dir() and not (vd / 'hf').exists():
        os.symlink(rv / 'hf', vd / 'hf')
    if esp32:
        tools = [p for p in (real / 'tools').glob('microWakeWord-*')] if (real / 'tools').is_dir() else []
        if tools:
            (root / 'tools').mkdir(exist_ok=True)
            os.symlink(tools[0], root / 'tools' / tools[0].name)
        neg = catalog.dataset_dir(real, 'mww_negatives')
        out = catalog.dataset_dir(root, 'mww_negatives')
        if not python:
            missing.append('mww_negatives (no plugin env python)')
        else:
            import subprocess
            code = ("import sys, numpy as np\nfrom pathlib import Path\nfrom mmap_ninja.ragged import RaggedMmap\n"
                    "real, out = Path(sys.argv[1]), Path(sys.argv[2])\nrng = np.random.default_rng(1)\n"
                    "for name, part in (('speech','training'),('no_speech','training'),('dinner_party','training'),('dinner_party_eval','validation_ambient'),('dinner_party_eval','testing_ambient')):\n"
                    "    srcs = sorted((real / name / part).glob('*_mmap')) if (real / name / part).is_dir() else []\n"
                    "    d = out / name / part\n    d.mkdir(parents=True, exist_ok=True)\n"
                    "    if srcs:\n        rows = [np.asarray(x) for x in RaggedMmap(str(srcs[0]))[:40]]\n"
                    "    else:\n        rows = [rng.uniform(0, 26, (300, 40)).astype(np.float32) for _ in range(40)]\n"
                    "    RaggedMmap.from_lists(str(d / 'road_mmap'), rows)\n")
            r = subprocess.run([str(python), '-c', code, str(neg), str(out)], capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                missing.append(f"mww_negatives ({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'stand-in failed'})")
            else:
                catalog.write_receipt(root, 'mww_negatives', ['road_mmap'])
    return missing
