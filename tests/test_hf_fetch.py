"""core/hf_fetch.py: the download door. A real child process, a fake hub: the
worker is swapped for a script that plays a part named by the repo, per path
(HF_HUB_DISABLE_XET: '1' = classic, '0' = xet). Run with: pytest tests/test_hf_fetch.py -v"""
import sys
import textwrap

import pytest

from core import hf_fetch

FAKE = textwrap.dedent('''
    import json, os, sys, time
    job = json.loads(sys.argv[1])
    classic = os.environ['HF_HUB_DISABLE_XET'] == '1'
    part = dict(p.split('=') for p in job['repo'].split(','))['classic' if classic else 'xet']
    def say(**m): print(json.dumps(m), flush=True)
    if part == 'mute': time.sleep(60)
    say(hello=True)
    say(host='cdn.example' if classic else 'cas.example')
    if part == 'ok':
        say(file='model.bin'); say(n=5, total=10); say(n=10, total=10)
        say(ok='/cache/classic' if classic else '/cache/xet')
    elif part == 'hang': time.sleep(60)
    elif part == 'error': say(error='connection reset', fatal=False)
    elif part == 'fatal': say(error='no such model', fatal=True)
    elif part == 'dies': sys.exit(3)
''')


class Row:
    def __init__(self):
        self.id, self.said = None, []

    def name(self, what): self.said.append(('name', what))
    def bytes(self, done, total=None): self.said.append(('bytes', done, total))


@pytest.fixture
def door(tmp_path, monkeypatch):
    worker = tmp_path / 'fake_worker.py'
    worker.write_text(FAKE, encoding='utf-8')
    monkeypatch.setattr(hf_fetch, 'WORKER', worker)
    monkeypatch.setattr(hf_fetch, 'LOG', tmp_path / 'hf_fetch.log')
    monkeypatch.setattr(hf_fetch, '_routes', lambda: [('classic', '1'), ('xet', '0')])
    monkeypatch.setattr(hf_fetch, '_locks', {})
    asked = []
    from core import net_check
    monkeypatch.setattr(net_check, 'check', lambda hosts: (asked.append(list(hosts)), ('stale-dns', 'DNS words.'))[1])
    hf_fetch.asked = asked
    return hf_fetch


def test_the_first_path_working_is_the_whole_story(door):
    row = Row()
    assert door.fetch('classic=ok,xet=fatal', patterns=['model.bin'], handle=row, stall=5) == '/cache/classic'
    assert row.said == [('name', 'model.bin'), ('bytes', 5, 10), ('bytes', 10, 10)]
    assert door.asked == []                 # a healthy download asks nobody anything


def test_a_path_that_stops_moving_is_ended_and_the_other_tried(door):
    assert door.fetch('classic=hang,xet=ok', patterns=['model.bin'], stall=0.6) == '/cache/xet'


def test_a_path_that_errors_hands_over_too(door):
    assert door.fetch('classic=error,xet=ok', files=['model.bin'], stall=5) == '/cache/xet'


def test_a_child_that_dies_hands_over_too(door):
    assert door.fetch('classic=dies,xet=ok', files=['model.bin'], stall=5) == '/cache/xet'


def test_both_paths_dead_fails_with_the_network_reason_about_every_host_seen(door):
    with pytest.raises(door.FetchError) as e:
        door.fetch('classic=hang,xet=hang', patterns=['model.bin'], stall=0.6)
    assert str(e.value) == 'DNS words.' and e.value.verdict == 'stale-dns'
    assert door.asked == [['cas.example', 'cdn.example', 'huggingface.co']]


def test_a_reason_that_is_the_same_on_any_path_fails_at_once(door):
    with pytest.raises(door.FetchError) as e:
        door.fetch('classic=fatal,xet=ok', files=['nope.bin'], stall=5)
    assert str(e.value) == 'no such model' and door.asked == []


def test_servers_that_answer_keep_the_last_error_in_the_words(door, monkeypatch):
    from core import net_check
    monkeypatch.setattr(net_check, 'check', lambda hosts: ('ok', 'The servers answer.'))
    with pytest.raises(door.FetchError) as e:
        door.fetch('classic=error,xet=error', files=['model.bin'], stall=5)
    assert str(e.value) == "The download from Hugging Face failed: connection reset. The servers answer."


def test_a_child_that_never_says_hello_is_a_stall_on_the_start_clock(door, monkeypatch):
    monkeypatch.setattr(door, 'START_S', 0.6)
    assert door.fetch('classic=mute,xet=ok', files=['model.bin'], stall=30) == '/cache/xet'


def test_the_row_turns_stalled_while_the_other_path_is_tried(door, monkeypatch):
    from core import notify
    said = []
    monkeypatch.setattr(notify, 'tick', lambda rid, **kw: said.append((rid, kw)))
    row = Row()
    row.id = 'r1'
    door.fetch('classic=hang,xet=ok', patterns=['model.bin'], handle=row, stall=0.6)
    assert said == [('r1', {'phase': 'stalled', 'text': "No movement on the usual download path. Trying the other one."})]


def test_without_xet_installed_there_is_one_path(monkeypatch):
    monkeypatch.setattr(hf_fetch.importlib.util, 'find_spec', lambda name: None)
    assert hf_fetch._routes() == [('classic', '1')]


def test_behind_a_proxy_there_is_the_classic_path_only(monkeypatch):
    """SOCKS mode promises nothing goes around the proxy; the xet client is not known to ride it."""
    from core import net_check
    monkeypatch.setattr(hf_fetch.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(net_check, '_proxied', lambda: True)
    assert hf_fetch._routes() == [('classic', '1')]
    monkeypatch.setattr(net_check, '_proxied', lambda: False)
    assert hf_fetch._routes() == [('classic', '1'), ('xet', '0')]


@pytest.mark.parametrize('has_xet', [True, False])
def test_a_job_starts_clean_exactly_where_the_xet_path_exists(door, monkeypatch, has_xet):
    jobs = []
    monkeypatch.setattr(hf_fetch, '_has_xet', lambda: has_xet)
    monkeypatch.setattr(hf_fetch, '_run', lambda job, *a: (jobs.append(job), ('ok', '/cache'))[1])
    door.fetch('some/repo', files=['model.onnx'])
    assert jobs[0]['clean'] is has_xet


def _play(job, env=None):
    """The real worker, one job, every line it says up to its verdict."""
    import json, os, subprocess
    proc = subprocess.Popen([sys.executable, str(hf_fetch.ROOT / 'core' / 'hf_fetch_worker.py'), json.dumps(job)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            cwd=str(hf_fetch.ROOT), env=dict(os.environ, **(env or {})))
    try:
        lines = [json.loads(proc.stdout.readline())]
        while 'error' not in lines[-1] and 'ok' not in lines[-1]:
            lines.append(json.loads(proc.stdout.readline()))
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdin.close(), proc.stdout.close()
    return lines


@pytest.mark.parametrize('clean', [True, False])
def test_a_clean_start_removes_that_repos_partial_files_and_nothing_else(tmp_path, clean):
    """A partial file left by the xet path is not the start of the file; the classic path
    would resume it by its size. No files asked for: the worker never reaches the network."""
    mine = tmp_path / 'hub' / 'models--some--repo' / 'blobs'
    other = tmp_path / 'hub' / 'models--other--repo' / 'blobs'
    for d in (mine, other):
        d.mkdir(parents=True)
        (d / 'abc123.incomplete').write_bytes(b'part')
    (mine / 'def456').write_bytes(b'a finished file')
    lines = _play({'repo': 'some/repo', 'files': [], 'patterns': [], 'clean': clean},
                  env={'HF_HOME': str(tmp_path), 'HF_HUB_CACHE': str(tmp_path / 'hub'), 'HF_HUB_OFFLINE': '1'})
    assert lines == [{'hello': True}, {'ok': []}]
    assert (mine / 'abc123.incomplete').exists() is (not clean)
    assert (mine / 'def456').exists() and (other / 'abc123.incomplete').exists()


def test_the_worker_counts_bytes_in_small_chunks_and_the_hub_still_reads_that_setting():
    """The hub counts a chunk when it is whole, and its own chunk is 10 MB: on a slow line
    that is longer than STALL_S between two counts, and a moving download was ended as
    stalled. The worker sets a small one. This also pins what that leans on: the hub
    reading the constant at download time (it is not pinned in requirements.txt)."""
    import inspect, re
    from huggingface_hub import constants, file_download
    src = (hf_fetch.ROOT / 'core' / 'hf_fetch_worker.py').read_text(encoding='utf-8')
    chunk = eval(re.search(r'^CHUNK = (.+)$', src, re.M).group(1))
    assert 'constants.DOWNLOAD_CHUNK_SIZE = CHUNK' in src
    assert chunk * 8 / hf_fetch.STALL_S < 64_000            # only a line slower than 64 kbit/s goes quiet that long
    assert constants.DOWNLOAD_CHUNK_SIZE > chunk            # the library's own is the large one
    assert 'constants.DOWNLOAD_CHUNK_SIZE' in inspect.getsource(file_download.http_get)


def test_the_real_worker_is_a_script_this_python_can_start():
    """The protocol's first line needs no network: a bad job fails in words, not a traceback."""
    import json, subprocess
    proc = subprocess.Popen([sys.executable, str(hf_fetch.ROOT / 'core' / 'hf_fetch_worker.py'),
                             json.dumps({'files': ['model.bin']})],          # no repo: fails before any network
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            cwd=str(hf_fetch.ROOT))
    try:
        lines = [json.loads(proc.stdout.readline())]
        while 'error' not in lines[-1] and 'ok' not in lines[-1]:
            lines.append(json.loads(proc.stdout.readline()))
    finally:
        proc.kill()
        proc.wait(timeout=10)
        proc.stdin.close(), proc.stdout.close()
    assert lines[0] == {'hello': True} and {'file': 'model.bin'} in lines
    assert 'error' in lines[-1] and lines[-1]['fatal'] is False
