"""core.tts.kokoro_files: the parent fetches Kokoro's model before the worker
spawns, under a notification-center progress row (user report 2026-10-20: a
first boot on a slow line looked like Kokoro never coming up, and the worker
is a subprocess with no line to the center)."""
import inspect

from core import notify
from core.tts import kokoro_files as kf


def test_missing_lists_uncached_files_in_download_order(monkeypatch):
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, 'try_to_load_from_cache',
                        lambda repo, fn: f'/cache/{fn}' if fn == kf.CONFIG_FILE else None)
    assert kf.missing() == [kf.MODEL_FILE, kf.voice_file(kf.DEFAULT_VOICE)]
    monkeypatch.setattr(huggingface_hub, 'try_to_load_from_cache', lambda repo, fn: f'/cache/{fn}')
    assert kf.missing(('af_heart', 'am_adam')) == []


def test_fetch_is_one_row_that_ends_ready(monkeypatch):
    import huggingface_hub
    got = []

    def fake_download(repo, fn, tqdm_class=None):
        assert repo == kf.REPO_ID
        bar = tqdm_class(total=100, unit='B')
        bar.update(60)
        bar.update(40)
        got.append(fn)
        return f'/dl/{fn}'

    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', fake_download)
    assert kf.fetch([kf.MODEL_FILE, kf.voice_file('af_heart')]) is True
    assert got == [kf.MODEL_FILE, 'voices/af_heart.pt']
    rows = [r for r in notify.rows() if r.get('key') == 'kokoro-model']
    assert len(rows) == 1
    r = rows[0]
    assert r['state'] == 'done' and r['level'] == 'success' and r['title'] == 'Kokoro voice model ready'
    assert r['progress']['percent'] == 100 and r['go']['tab'] == 'tts'


def test_fetch_failure_is_a_failed_row_and_false(monkeypatch):
    import huggingface_hub

    def boom(repo, fn, tqdm_class=None):
        raise ConnectionError('hub unreachable')

    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', boom)
    assert kf.fetch([kf.MODEL_FILE]) is False
    r = next(r for r in notify.rows() if r.get('key') == 'kokoro-model')
    assert r['state'] == 'failed' and r['level'] == 'error'
    assert r['title'] == 'Kokoro voice model download failed' and 'hub unreachable' in r['body']


def test_fetch_in_offline_mode_names_the_variable(monkeypatch):
    """The hub wraps its own offline mode in "check your Internet connection";
    the row says what is actually on and what to do (user report 2026-10-21)."""
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')

    def boom(repo, fn, tqdm_class=None):
        raise LocalEntryNotFoundError('... make sure your Internet connection is on.') from OfflineModeIsEnabled('Cannot reach')

    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', boom)
    assert kf.fetch([kf.voice_file('af_heart')]) is False
    r = next(r for r in notify.rows() if r.get('key') == 'kokoro-model')
    assert r['state'] == 'failed' and 'HF_HUB_OFFLINE' in r['body'] and 'offline mode is on' in r['body']
    assert 'Internet connection' not in r['body']


def test_nothing_to_fetch_makes_no_row():
    assert kf.fetch([]) is True
    assert not [r for r in notify.rows() if r.get('key') == 'kokoro-model']


def test_worker_shares_the_constants():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'core' / 'tts' / 'tts_server.py').read_text(encoding='utf-8')
    assert 'from core.tts.kokoro_files import' in src
    assert "REPO_ID = '" not in src and "DEFAULT_VOICE = '" not in src


def test_parent_fetches_off_the_boot_thread_with_a_generation_guard():
    import sapphire
    src = inspect.getsource(sapphire.VoiceChatSystem._start_kokoro_server)
    assert 'kokoro_files.missing(' in src and 'kokoro_files.fetch(' in src
    assert 'threading.Thread' in src and '_kokoro_gen' in src
    assert '_kokoro_gen' in inspect.getsource(sapphire.VoiceChatSystem._stop_kokoro_server)
