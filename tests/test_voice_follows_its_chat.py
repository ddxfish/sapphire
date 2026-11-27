"""Voice belongs to the chat it was started in (Krem, 2026-11-26).

Speech takes seconds: the wake word, the sentence, the transcription. The
chat it was meant for used to be read only when the turn started, so if the
vault idle-locked mid-sentence (or the app restarted under hands-free mode),
words spoken to a PRIVATE chat ran in the ordinary chat the pointer had been
moved to. Now voice remembers where it started; if that is no longer where it
would land, it is dropped: never sent, never stored, and the person is told.
"""
import inspect
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import voice_privacy as vp

STATIC = Path(__file__).parent.parent / "interfaces" / "web" / "static"


class _SM:
    def __init__(self):
        self.chats = {'bedroom': {'private_chat': True}, 'desk': {'private_chat': False},
                      'den': {'private_chat': False}}
        self.active = 'bedroom'

    def get_active_chat_name(self):
        return self.active

    def get_settings_for(self, name):
        return self.chats.get(name)


@pytest.fixture
def system():
    return types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=_SM()))


# ── the rule ──

def test_where_voice_lands_and_whether_that_chat_is_private(system):
    assert vp.voice_lands(system) == ('bedroom', True)
    system.llm_chat.session_manager.active = 'desk'
    assert vp.voice_lands(system) == ('desk', False)


def test_voice_has_moved_when_the_chat_it_started_in_is_not_where_it_lands_now(system):
    started = vp.voice_lands(system)                       # the wake word, in the private chat
    assert vp.voice_moved(system, started) is False
    system.llm_chat.session_manager.active = 'desk'        # the vault idle-locked mid-sentence
    assert vp.voice_moved(system, started) is True


def test_cannot_tell_counts_as_moved(system):
    assert vp.voice_moved(system, None) is True
    assert vp.voice_moved(system, (None, True)) is True
    started = vp.voice_lands(system)
    system.llm_chat.session_manager.get_active_chat_name = MagicMock(side_effect=RuntimeError("db"))
    assert vp.voice_lands(system) == (None, True)
    assert vp.voice_moved(system, started) is True


def test_a_chat_that_cannot_be_read_is_judged_private(system):
    system.llm_chat.session_manager.active = 'sealed-one'
    assert vp.voice_lands(system) == ('sealed-one', True)


# ── the wake word ──

def test_the_wake_word_reads_its_chat_at_the_wake_and_drops_words_whose_chat_left():
    from core.wakeword import wake_detector
    src = inspect.getsource(wake_detector)
    wake = src.index('started_in = voice_lands(self.system)')
    listen = src.index('Wake word detected! Starting to listen...')
    check = src.index('if voice_moved(self.system, started_in):')
    hook = src.index('hook_runner.fire("post_stt", stt_event)')
    turn = src.index('self.system.process_llm_query(text, voice_turn=True)')
    assert wake < listen, "read before a word is recorded"
    assert check < hook < turn, "checked before any plugin, any chat and any model hears the words"
    dropped = src[check:hook]
    assert 'VOICE_MOVED_MSG' in dropped and 'return' in dropped


# ── hands-free conversation mode ──

def _driver(system, chat_name=None):
    from core.conversation.driver import ConversationDriver
    d = ConversationDriver.__new__(ConversationDriver)
    d.system, d._chat_name = system, chat_name
    d._heard_in, d._session_over = None, False
    d._cue = lambda name: None
    return d


def test_hands_free_stops_when_the_private_chat_it_was_talking_in_is_gone(system):
    d = _driver(system)
    assert d._voice_gone() is False and d._heard_in == ('bedroom', True)
    system.llm_chat.session_manager.active = 'desk'        # the idle lock, or a restart: now an ordinary chat
    assert d._voice_gone() is True, "the next thing said must not go to the ordinary chat"
    assert d._session_over is True
    system.llm_chat.session_manager.active = 'bedroom'
    assert d._voice_gone() is True, "over is over: the person turns it on again"


def test_hands_free_still_follows_an_ordinary_chat_switch(system):
    sm = system.llm_chat.session_manager
    sm.active = 'desk'
    d = _driver(system)
    assert d._voice_gone() is False
    sm.active = 'den'
    assert d._voice_gone() is False and d._heard_in == ('den', False)
    sm.active = 'bedroom'                                   # into a private chat: allowed (local speech is gated elsewhere)
    assert d._voice_gone() is False and d._heard_in == ('bedroom', True)


def test_a_phone_calls_chat_is_fixed_and_not_this_rules_business(system):
    d = _driver(system, chat_name='desk')
    system.llm_chat.session_manager.active = 'den'
    assert d._voice_gone() is False


def test_the_hands_free_turn_checks_again_after_the_words_are_transcribed():
    from core.conversation import driver
    src = inspect.getsource(driver.ConversationDriver._run_turn)
    assert src.index('if self._voice_gone():') < src.index("_spoken_in = getattr(self, '_heard_in', None)")
    again = src.index('if voice_moved(self.system, _spoken_in):')
    assert again < src.index('transcribed user utterance -> streaming'), "before the turn is started"


# ── a restart ──

def _manager(vault, marker):
    from core.conversation.manager import ConversationManager
    m = ConversationManager.__new__(ConversationManager)
    sm = types.SimpleNamespace(boot_last_active=marker)
    m.system = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))
    m.start_local = MagicMock(return_value=True)
    return m


@pytest.mark.parametrize("vault, marker, resumes", [
    (True, None, False),          # the chat open at shutdown was private (its marker is blanked)
    (True, 'desk', True),         # an ordinary chat was open
    (True, 'default', True),
    (False, None, True),          # no vault: nothing is private in that sense
])
def test_local_hands_free_does_not_resume_by_itself_after_a_private_chat(monkeypatch, vault, marker, resumes):
    import config
    from core import prompt_vault
    monkeypatch.setattr(config, 'CONVERSATION_LAST_MODE', 'local', raising=False)
    monkeypatch.setattr(prompt_vault, 'vault_exists', lambda: vault)
    m = _manager(vault, marker)
    m.resume()
    assert m.start_local.called is resumes


def test_the_store_remembers_what_the_marker_said_at_boot():
    import core.chat.history as hist
    src = inspect.getsource(hist)        # (the test harness wraps __init__ itself)
    assert 'self.boot_last_active = last_active' in src


# ── the browser ──

def test_the_mic_button_carries_the_chat_from_the_press():
    a = (STATIC / "audio.js").read_text(encoding='utf-8')
    press = a.split('export const handlePress', 1)[1].split('export const handleRelease', 1)[0]
    assert '_spokenIn = getViewedChat();' in press
    release = a.split('export const handleRelease', 1)[1]
    assert release.count('getViewedChat() !== spokenIn') >= 2, "checked before the audio leaves and before the words are sent"
    assert 'api.postAudio(blob, spokenIn)' in release
    api = (STATIC / "api.js").read_text(encoding='utf-8')
    assert 'export const postAudio = async (blob, chat = null)' in api and 'chat || getViewedChat()' in api


def test_browser_hands_free_does_not_reconnect_into_a_different_chat():
    c = (STATIC / "features" / "conversation.js").read_text(encoding='utf-8')
    assert 'chatAtDrop = getViewedChat();' in c
    retry = c.split('retryTimer = setTimeout(async () => {', 1)[1].split('}, ms);', 1)[0]
    assert 'await _sameChatStill()' in retry and 'stop();' in retry
    assert retry.index('_sameChatStill') < retry.index('_connect()'), "checked before the mic is connected again"
