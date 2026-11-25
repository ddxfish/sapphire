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


def test_fetch_is_one_row_through_the_door_that_ends_ready(monkeypatch):
    from core import hf_fetch
    got = []

    def fake_fetch(repo, files=(), handle=None, **kw):
        assert repo == kf.REPO_ID
        for f in files:
            handle.name(f)
            handle.bytes(60, 100)
            handle.bytes(100, 100)
            got.append(f)
        return [f'/dl/{f}' for f in files]

    monkeypatch.setattr(hf_fetch, 'fetch', fake_fetch)
    assert kf.fetch([kf.MODEL_FILE, kf.voice_file('af_heart')]) is True and kf.last_error is None
    assert got == [kf.MODEL_FILE, 'voices/af_heart.pt']
    rows = [r for r in notify.rows() if r.get('key') == 'kokoro-model']
    assert len(rows) == 1
    r = rows[0]
    assert r['state'] == 'done' and r['level'] == 'success' and r['title'] == 'Kokoro voice model ready'
    assert r['progress']['percent'] == 100 and r['go']['tab'] == 'tts' and r['action'] is None


def test_fetch_failure_is_a_failed_row_with_a_retry_and_false(monkeypatch):
    from core import hf_fetch

    def boom(repo, **kw):
        raise hf_fetch.FetchError("This network's DNS is giving out-of-date addresses.", 'stale-dns')

    monkeypatch.setattr(hf_fetch, 'fetch', boom)
    assert kf.fetch([kf.MODEL_FILE]) is False
    assert kf.last_error == "This network's DNS is giving out-of-date addresses."
    r = next(r for r in notify.rows() if r.get('key') == 'kokoro-model')
    assert r['state'] == 'failed' and r['level'] == 'error'
    assert r['title'] == 'Kokoro voice model download failed' and 'out-of-date addresses' in r['body']
    assert r['action'] == {'repair': 'switch:TTS_PROVIDER=kokoro', 'label': 'Try again'}


def test_offline_mode_words_from_the_child_reach_the_row(monkeypatch):
    """The download runs in a child now, so the hub's exception chain never
    crosses over: the child turns it into words (core/hf_words.py, the user
    report of 2026-10-21) and they arrive whole."""
    from core import hf_fetch
    from core.hf_words import why
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    try:
        raise LocalEntryNotFoundError('... make sure your Internet connection is on.') from OfflineModeIsEnabled('Cannot reach')
    except LocalEntryNotFoundError as e:
        words = why(e)                      # what core/hf_fetch_worker.py sends
    monkeypatch.setattr(hf_fetch, 'fetch', lambda repo, **kw: (_ for _ in ()).throw(hf_fetch.FetchError(words)))
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
    # a failed fetch leaves the worker off and says why to the switch that waits
    assert 'if not ok:' in src and 'kokoro_why' in src and src.index('if not ok:') < src.rindex('_spawn()')
    assert '_kokoro_gen' in inspect.getsource(sapphire.VoiceChatSystem._stop_kokoro_server)
