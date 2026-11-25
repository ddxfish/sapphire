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
