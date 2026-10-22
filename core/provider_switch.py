# core/provider_switch.py - a provider switch is a JOB with a row (tmp/setup-wizard-plan.md §3).
#
# STT_PROVIDER, TTS_PROVIDER, EMBEDDING_PROVIDER, WAKE_WORD_ENABLED and WAKEWORD_MODEL
# (and the legacy STT_ENABLED / TTS_ENABLED toggles, which map onto their provider key)
# used to switch INSIDE the settings request: Save blocked for the whole model download,
# the browser's 60 s fetch timeout said "Save failed" while the server quietly succeeded,
# and a failed switch toasted success. Now the request returns at once:
#
#   jobs = start([(key, value), ...], persist=True)   -> {key: row_id}
#
# One daemon thread runs the items in order (wake word last: its audio and onnx init
# must not overlap another C extension's init, the 2026-04-25 SIGABRT), every item under
# its own notification row (core/notify.py), and ALL jobs serialize app-wide on _LOCK.
#
# Krem's rulings (2026-10-22):
#   B1 really ready: a row ends "done" only when the provider answers (Kokoro's worker
#      is polled on is_available(); the embedder is warmed so its model downloads now,
#      under its own row; a Whisper that fell to Null is a failure, not a success).
#   B4 config after success: the provider key lands in config only when the switch
#      succeeded, so core/voice_privacy.py's gate (which reads config) and the live
#      client agree for the whole load. WAKEWORD_MODEL is the exception: the reload reads
#      its name from config, so it is set first and put back on failure.
#   Persist on success. On failure the live side is brought back to the previous
#      provider when that can be re-made, else off, and config follows the live side.
#   B3 reload callbacks (a plugin unload, the file watcher) ENQUEUE through enqueue(key):
#      they fire inside settings._lock, so switching inline there would deadlock the
#      persist; the job thread takes the lock later, briefly.
#   settings_changed fires on finish with the LIVE value and ok: True|False.
#
# A second request for a key already switching: the same value joins (its row id comes
# back), a different value is refused (SwitchInProgress -> 409 at the routes).
import logging
import threading
import time

import config
from core import notify

logger = logging.getLogger(__name__)

KEYS = ('STT_PROVIDER', 'TTS_PROVIDER', 'EMBEDDING_PROVIDER', 'WAKE_WORD_ENABLED', 'WAKEWORD_MODEL')
WAKEWORD_KEYS = ('WAKE_WORD_ENABLED', 'WAKEWORD_MODEL')
CONFIG_FIRST = ('WAKEWORD_MODEL',)        # the switch reads the value from config
TTS_READY_S = 20 * 60                     # a first Kokoro boot downloads its model under its own row
TTS_READY_POLL_S = 1.0

_LOCK = threading.Lock()                  # every switch job, app-wide, one at a time
_GUARD = threading.Lock()                 # _inflight
_inflight = {}                            # key -> {'value', 'rid', 'thread'}

_TAB = {'STT_PROVIDER': 'stt', 'TTS_PROVIDER': 'tts', 'EMBEDDING_PROVIDER': 'embedding',
        'WAKE_WORD_ENABLED': 'wakeword', 'WAKEWORD_MODEL': 'wakeword'}
_ROW_KEY = {'STT_PROVIDER': 'switch-stt', 'TTS_PROVIDER': 'switch-tts', 'EMBEDDING_PROVIDER': 'switch-embedding',
            'WAKE_WORD_ENABLED': 'switch-wakeword', 'WAKEWORD_MODEL': 'switch-wakeword-model'}
_WHAT = {'STT_PROVIDER': 'speech-to-text', 'TTS_PROVIDER': 'voice', 'EMBEDDING_PROVIDER': 'embeddings'}


class SwitchInProgress(Exception):
    def __init__(self, key, value):
        super().__init__(f"{key} is already switching to '{value}'; wait for it to finish")
        self.key, self.value = key, value


# ── what a request becomes ─────────────────────────────────────────────────

def item_for(key, value):
    """(provider_key, provider_value) when `key` is a switch, else None. The legacy
    STT_ENABLED / TTS_ENABLED toggles map onto their provider key the way
    toggle_stt / toggle_tts did: on = the configured provider (or the local default
    when none), off = 'none'."""
    if key in KEYS:
        return (key, bool(value) if key == 'WAKE_WORD_ENABLED' else value)
    if key == 'STT_ENABLED':
        return ('STT_PROVIDER', _toggle_target('STT_PROVIDER', value, 'faster_whisper'))
    if key == 'TTS_ENABLED':
        return ('TTS_PROVIDER', _toggle_target('TTS_PROVIDER', value, 'kokoro'))
    return None


def _toggle_target(provider_key, enabled, default):
    if not enabled:
        return 'none'
    cur = getattr(config, provider_key, None)
    return cur if cur and cur != 'none' else default


def start(items, persist=True):
    """Queue the switches. Returns {key: row_id}. Raises SwitchInProgress when a key is
    already switching to a different value (nothing is queued then)."""
    items = [(k, v) for k, v in items if k in KEYS]
    # Wake word last: the invariant tests/test_deferred_actions_ordering.py pins.
    items.sort(key=lambda kv: kv[0] in WAKEWORD_KEYS)
    jobs, fresh = {}, []
    with _GUARD:
        for key, value in items:
            cur = _inflight.get(key)
            if cur and cur['value'] != value:
                raise SwitchInProgress(key, cur['value'])
        for key, value in items:
            cur = _inflight.get(key)
            if cur:
                jobs[key] = cur['rid']          # the same switch: join it
                continue
            rid = notify.begin(_active_title(key, value), source='core',
                               go={'view': 'settings', 'tab': _TAB[key]}, key=_ROW_KEY[key])
            _inflight[key] = {'value': value, 'rid': rid, 'thread': None}
            jobs[key] = rid
            fresh.append((key, value, rid))
        if fresh:
            t = threading.Thread(target=_run, args=(fresh, persist), name='provider-switch', daemon=True)
            for key, _, _ in fresh:
                _inflight[key]['thread'] = t
    if fresh:
        t.start()
    return jobs


def enqueue(key):
    """A reload callback for settings.register_reload_callback: the value is already
    in config (the caller set it), the job only makes the live side match."""
    def _cb(value):
        try:
            start([(key, value)], persist=False)
        except SwitchInProgress as e:
            logger.warning(f"[SWITCH] {e}")
    return _cb


def wait(key=None, timeout=None):
    """Join the running job(s): tests, and a caller that must see the outcome."""
    with _GUARD:
        threads = {v['thread'] for k, v in _inflight.items() if (key is None or k == key) and v['thread']}
    for t in threads:
        t.join(timeout)


def busy(key):
    with _GUARD:
        cur = _inflight.get(key)
        return dict(cur) if cur else None


# ── the thread ─────────────────────────────────────────────────────────────

def _run(fresh, persist):
    for key, value, rid in fresh:
        with _LOCK:
            try:
                _run_one(key, value, rid, persist)
            except Exception as e:                # the job's own bug: the row must still end
                logger.exception(f"[SWITCH] {key} -> {value!r} crashed")
                notify.finish(rid, failed=True, title=f"{_active_title(key, value)} failed", body=str(e))
            finally:
                with _GUARD:
                    _inflight.pop(key, None)


def _run_one(key, value, rid, persist):
    from core.settings_manager import settings
    system = _system()
    prev = settings.get(key)
    tier = settings.validate_tier(key)
    try:
        ok, why = _apply(system, key, value, rid)
    except Exception as e:
        ok, why = False, _words(e)
    if ok:
        if persist or settings.get(key) != value:
            settings.set(key, value, persist=persist, _skip_callbacks=True)
        if key == 'TTS_PROVIDER':
            _reapply_chat(system)
        notify.finish(rid, title=_done_title(key, value))
        _publish(key, value, tier, True)
        logger.info(f"[SWITCH] {key} -> {value!r} ok")
        return
    live = _recover(system, key, prev, value)
    if settings.get(key) != live:
        settings.set(key, live, persist=True, _skip_callbacks=True)
    kept = 'off' if live in ('none', False, None) else _label(key, live)
    logger.error(f"[SWITCH] {key} -> {value!r} failed: {why}; kept {kept}")
    notify.finish(rid, failed=True, title=f"{_active_title(key, value)} failed",
                  body=f"{why}. Kept {kept}.")
    _publish(key, live, tier, False, why)


def _apply(system, key, value, rid=None):
    """Run the switch and wait until the provider is really there. (ok, why)."""
    from core.settings_manager import settings
    if key in CONFIG_FIRST:
        settings.set(key, value, persist=False, _skip_callbacks=True)
    if key == 'STT_PROVIDER':
        prev = getattr(config, key, None)
        if system.switch_stt_provider(value) is False:
            return False, "the switch reported failure"
        ok, why = _stt_ready(system, value)
        if ok and value != 'none' and prev in (None, 'none'):
            _warm_vad()
        return ok, why
    if key == 'TTS_PROVIDER':
        if system.switch_tts_provider(value) is False:
            return False, "the switch reported failure"
        return _tts_ready(system, value, rid)
    if key == 'EMBEDDING_PROVIDER':
        system.switch_embedding_provider(value)       # raises on a Null fallback
        return _embedding_ready(system, value)
    if key == 'WAKE_WORD_ENABLED':
        if system.toggle_wakeword(bool(value)) is False:
            return False, "the wake word could not start (the microphone, or the models)"
        return True, ''
    if key == 'WAKEWORD_MODEL':
        if system.reload_wakeword_model(value) is False:
            return False, "the detector could not load that model"
        return True, ''
    return False, f"not a switch: {key}"


def _recover(system, key, prev, failed):
    """The live side after a failed switch: `prev` when it can be re-made, else off."""
    off = False if key == 'WAKE_WORD_ENABLED' else 'none'
    for cand in [c for c in (prev, off) if c is not None and c != failed]:
        try:
            ok, _ = _apply(system, key, cand)
            if ok:
                return cand
        except Exception as e:
            logger.warning(f"[SWITCH] {key}: could not go back to {cand!r}: {e}")
    return off


# ── readiness (B1) ─────────────────────────────────────────────────────────

def _stt_ready(system, value):
    if value == 'none':
        return True, ''
    from core.stt.stt_null import NullWhisperClient
    client = getattr(system, 'whisper_client', None)
    if client is None or isinstance(client, NullWhisperClient):
        return False, "the provider failed to initialize (fell back to null)"
    try:
        if client.is_available():
            return True, ''
    except Exception as e:
        return False, _words(e)
    return False, "the provider is not available (its key, or its server)"


def _tts_ready(system, value, rid=None):
    if value == 'none':
        return True, ''
    from core.tts.providers.null import NullTTSProvider
    prov = getattr(getattr(system, 'tts', None), '_provider', None)
    if prov is None or isinstance(prov, NullTTSProvider):
        return False, "the provider failed to initialize (fell back to null)"
    deadline = time.monotonic() + TTS_READY_S
    told = False
    while True:
        try:
            if prov.is_available():
                return True, ''
            why = getattr(prov, '_health_why', None) or "no answer yet"
        except Exception as e:
            why = _words(e)
        if time.monotonic() >= deadline:
            return False, f"not ready after {TTS_READY_S // 60} minutes ({why})"
        if rid and not told:
            notify.tick(rid, text=f"waiting for {_label('TTS_PROVIDER', value)} to answer")
            told = True
        time.sleep(TTS_READY_POLL_S)


def _embedding_ready(system, value):
    if value == 'none':
        return True, ''
    from core.embeddings import get_embedder
    emb = get_embedder()
    avail = getattr(emb, 'available', True)
    if callable(avail):
        avail = avail()
    if avail:
        return True, ''
    return False, getattr(emb, 'load_error', None) or "the embedder is not available"


def _warm_vad():
    """STT came on after a boot without it: Silero was only ever warmed at boot."""
    try:
        from core.stt import silero_vad
        if not silero_vad.is_available():
            silero_vad.warmup_async()
    except Exception as e:
        logger.debug(f"[SWITCH] silero warm-up skipped: {e}")


# ── words ──────────────────────────────────────────────────────────────────

def _label(key, value):
    if value in ('none', None, False):
        return 'off'
    if value is True:
        return 'on'
    try:
        if key == 'STT_PROVIDER':
            from core.stt.providers import stt_registry as reg
        elif key == 'TTS_PROVIDER':
            from core.tts.providers import tts_registry as reg
        elif key == 'EMBEDDING_PROVIDER':
            from core.embeddings import embedding_registry as reg
        else:
            return str(value)
        reg.get_all()
        return (reg.get_entry(value) or {}).get('display_name') or str(value)
    except Exception:
        return str(value)


def _active_title(key, value):
    if key == 'WAKE_WORD_ENABLED':
        return "Starting the wake word" if value else "Stopping the wake word"
    if key == 'WAKEWORD_MODEL':
        return f"Switching the wake word to {value}"
    what = _WHAT[key]
    return f"Turning {what} off" if value in ('none', None) else f"Switching {what} to {_label(key, value)}"


def _done_title(key, value):
    if key == 'WAKE_WORD_ENABLED':
        return "Wake word listening" if value else "Wake word off"
    if key == 'WAKEWORD_MODEL':
        return f"Wake word: {value}"
    what = _WHAT[key].capitalize()
    return f"{what} off" if value in ('none', None) else f"{what}: {_label(key, value)}"


def _words(e):
    try:
        from core.hf_words import why
        return why(e)
    except Exception:
        return str(e) or type(e).__name__


# ── seams ──────────────────────────────────────────────────────────────────

def _system():
    from core.api_fastapi import get_system
    return get_system()


def _reapply_chat(system):
    """Voice / pitch / speed re-validated for the new provider (once; the route used
    to do it after switch_tts_provider's own partial reapply)."""
    try:
        from core.api_fastapi import _apply_chat_settings
        chat_settings = system.llm_chat.session_manager.get_chat_settings()
        _apply_chat_settings(system, chat_settings)
    except Exception as e:
        logger.warning(f"[SWITCH] re-applying chat settings after the TTS switch failed: {e}")


def _publish(key, value, tier, ok, error=''):
    try:
        from core.event_bus import publish, Events
        payload = {"key": key, "value": value, "tier": tier, "ok": ok}
        if error:
            payload["error"] = error
        publish(Events.SETTINGS_CHANGED, payload)
    except Exception as e:
        logger.debug(f"[SWITCH] publish failed: {e}")
