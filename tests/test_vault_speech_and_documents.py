"""Isolation hunt wave 4 (2026-11-26), the parts that are not her memory:
privacy has to travel with the work when the work runs somewhere else.

E2  a private chat's documents were embedded at upload and on the heal
    thread, outside any turn, so a remote embedder got their text.
E3  the wake word and the browser mic judged the OPEN chat while the words
    were for another (a private room on screen): the audio went to a cloud
    transcriber.
E4  the browser's whole-reply TTS request named no chat, so it was judged on
    the pointer: a private room's reply text went to a cloud voice.
"""
import asyncio
import types
from pathlib import Path

import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
from core import voice_privacy as vp

STATIC = Path(__file__).parent.parent / "interfaces" / "web" / "static"


# ── E2: documents ──

class _Embedder:
    """Records whether each embed ran as a private turn, and refuses like the
    real remote embedder does when it is one (core/embeddings)."""
    available = True
    provider_id = 'far'
    dimension = 3

    def __init__(self, far=True):
        self.far, self.calls = far, []

    def embed(self, texts, prefix='search_document'):
        from core.chat.function_manager import scope_private
        private = scope_private.get()
        self.calls.append(private)
        if private and self.far:
            return None
        import numpy as np
        return np.ones((len(texts), 3), dtype=np.float32)


@pytest.fixture
def docs(monkeypatch):
    import core.documents as d
    import core.api_fastapi as af
    emb = _Embedder()
    monkeypatch.setattr(d, '_kb', lambda: types.SimpleNamespace(_get_embedder=lambda: emb))
    chats = {'secret': {'private_chat': True}, 'desk': {'private_chat': False}}
    sm = types.SimpleNamespace(get_settings_for=lambda name: chats.get(name))
    monkeypatch.setattr(af, 'get_system', lambda: types.SimpleNamespace(
        llm_chat=types.SimpleNamespace(session_manager=sm)))
    monkeypatch.setattr(d, '_kept_near', {})
    return d, emb


def test_e2_a_private_chats_document_never_reaches_a_far_embedder(docs):
    d, emb = docs
    with d._for_chat('secret'):
        out = d._embed(['private page one', 'private page two'])
    assert emb.calls == [True], "the embedder was not told this text is private"
    from core.embeddings import KEPT_NEAR
    assert out == [(None, KEPT_NEAR, None)] * 2, "kept, and the chunks say so: keyword search only"


def test_e2_a_near_embedder_still_embeds_it(docs):
    d, emb = docs
    emb.far = False
    with d._for_chat('secret'):
        out = d._embed(['private page'])
    assert emb.calls == [True] and out[0][0] is not None


def test_e2_a_public_chats_document_embeds_as_before(docs):
    d, emb = docs
    with d._for_chat('desk'):
        out = d._embed(['public page'])
    assert emb.calls == [False] and out[0][0] is not None


def test_e2_a_chat_that_cannot_be_read_counts_as_private(docs):
    d, emb = docs
    with d._for_chat('sealed-or-gone'):
        d._embed(['whose page?'])
    with d._for_chat(None):
        d._embed(['no chat named'])
    assert emb.calls == [True, True]


def test_e2_the_private_flag_does_not_outlive_the_embed(docs):
    from core.chat.function_manager import scope_private
    d, _ = docs
    with d._for_chat('secret'):
        d._embed(['private page'])
    assert scope_private.get() is False


def test_e2_every_embed_of_a_document_runs_for_its_chat():
    """Upload (with and without a progress bar) and the heal thread."""
    import inspect
    import core.documents as d
    src = inspect.getsource(d)
    calls = [ln for ln in src.splitlines() if 'stamps = _embed(' in ln]
    assert len(calls) == 3
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if 'stamps = _embed(' in ln:
            assert 'with _for_chat(chat):' in lines[i - 1], ln


def test_e2_keyword_only_is_a_steady_state_not_a_retry_every_turn(docs, monkeypatch):
    """The heal thread is not started again for a private chat whose text the
    same embedder was already refused."""
    d, emb = docs
    started = []
    monkeypatch.setattr(d.threading, 'Thread', lambda **kw: types.SimpleNamespace(
        start=lambda: started.append(kw.get('args'))))
    d._kept_near['secret'] = 'far'
    d._heal_later('secret')
    assert started == []
    emb.provider_id = 'another'          # a different embedder gets its one try
    d._heal_later('secret')
    assert started == [('secret',)]
    d._healing.discard('secret')


# ── E3: the speech-in gate judges the chat the words are for ──

@pytest.fixture
def speech(monkeypatch):
    """active chat 'desk' (public); 'room' is private; cloud STT and TTS."""
    chats = {'desk': {'private_chat': False}, 'room': {'private_chat': True}}
    state = {'landing': 'desk'}
    sm = types.SimpleNamespace(
        get_active_chat_name=lambda: 'desk',
        get_landing_chat_name=lambda: state['landing'],
        get_settings_for=lambda name: chats.get(name),
        get_chat_settings=lambda: chats['desk'])
    system = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
    refuse = lambda s=None: 'blocked: not local' if (s or {}).get('private_chat') else ''   # noqa: E731
    monkeypatch.setattr(vp, 'stt_gate_reason', refuse)
    monkeypatch.setattr(vp, 'tts_gate_reason', refuse)
    return system, state


def test_e3_words_for_a_private_room_are_refused_though_the_open_chat_is_public(speech):
    from core.routes.tts import _stt_gate_for
    system, state = speech
    assert _stt_gate_for(system) == '', "everything public: speak freely"
    assert _stt_gate_for(system, 'room'), "the tab's screen shows a private room"
    state['landing'] = 'room'
    assert _stt_gate_for(system), "voice lands in a private room (the wake word's case)"
    assert _stt_gate_for(system, 'desk'), "either one private refuses"


def test_e3_a_chat_that_cannot_be_read_fails_closed(speech):
    from core.routes.tts import _stt_gate_for
    system, _ = speech
    assert _stt_gate_for(system, 'sealed-or-gone')


def test_e3_the_transcribe_route_refuses_before_it_touches_the_audio(speech, monkeypatch):
    from fastapi import HTTPException
    import core.routes.tts as r
    system, _ = speech
    monkeypatch.setattr(r, 'check_endpoint_rate', lambda *a, **k: None)
    monkeypatch.setattr(r, 'can_transcribe', lambda client: (True, ''))
    system.whisper_client = object()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.handle_transcribe(None, audio=None, chat='room', _=None, system=system))
    assert ei.value.status_code == 403


def test_e3_the_wake_word_asks_the_same_gate():
    src = (Path(__file__).parent.parent / "core" / "wakeword" / "wake_detector.py").read_text(encoding='utf-8')
    assert '_stt_gate_for(self.system)' in src
    assert 'stt_gate_reason(self.system.llm_chat.session_manager.get_chat_settings())' not in src


def test_e3_the_browser_mic_names_the_chat_on_screen():
    api = (STATIC / 'api.js').read_text(encoding='utf-8')
    post = api.split('export const postAudio', 1)[1].split('export const ', 1)[0]
    assert "form.append('chat', viewed)" in post


# ── E4: the whole-reply TTS request names its chat ──

def _speak(system, monkeypatch, body):
    import core.routes.tts as r
    monkeypatch.setattr(r, 'check_endpoint_rate', lambda *a, **k: None)
    monkeypatch.setattr(r.config, 'TTS_ENABLED', True, raising=False)

    class _Req:
        async def json(self):
            return body
    return asyncio.run(r.handle_tts_speak(_Req(), _=None, system=system))


def test_e4_a_private_rooms_reply_is_not_sent_to_a_cloud_voice(speech, monkeypatch):
    from fastapi import HTTPException
    system, _ = speech
    with pytest.raises(HTTPException) as ei:
        _speak(system, monkeypatch, {'text': 'her private reply', 'output_mode': 'file', 'chat': 'room'})
    assert ei.value.status_code == 403


def test_e4_a_reply_from_a_chat_that_cannot_be_read_is_refused(speech, monkeypatch):
    from fastapi import HTTPException
    system, _ = speech
    with pytest.raises(HTTPException) as ei:
        _speak(system, monkeypatch, {'text': 'whose reply?', 'output_mode': 'file', 'chat': 'sealed-or-gone'})
    assert ei.value.status_code == 403


def test_e4_the_browser_names_the_chat_on_screen():
    api = (STATIC / 'api.js').read_text(encoding='utf-8')
    fetch = api.split('export const fetchAudio', 1)[1].split('export const ', 1)[0]
    assert 'opts?.chat || getViewedChat()' in fetch and 'body.chat = said' in fetch
