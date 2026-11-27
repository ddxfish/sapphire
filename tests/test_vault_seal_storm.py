"""Try to make the vault leak (Krem, 2026-11-26): close it at awkward moments
and then look everywhere plaintext lives.

Real pieces: vault crypto, the chat store on a tmp database, LLMChat's
begin_stream / end_stream (the leases) and THE turn engine (chat_stream).
Doubles: the provider (a generator this file steers) and the tool manager.

Every scenario plants CANARY in a private chat's turn (the words the person
typed and the words she answered), seals the vault somewhere it hurts, and
then asserts the canary is in none of:
  - the chat database's raw bytes (file + WAL): plaintext at rest
  - the public chat's rows and the pointer's in-memory buffer
  - the plaintext prompt store
  - the event bus's replay ring (what a tab opened later is handed)
and that the vault really is locked. `test_the_scan_can_see_plaintext` proves
the scan is not blind.
"""
import json
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_crud as pc
import core.prompt_vault as pv
from core.prompt_manager import prompt_manager as pm
from test_vault_isolation_hunt import store, _idle_seal_done  # noqa: F401  (the fixture rides along)

CANARY = "CANARY-7f3a-PRIVATE-WORDS"
TYPED = f"my secret is {CANARY} (typed)"
SAID = [f"I will keep {CANARY} ", "safe ", "with me."]


# ── the rig ──

def _rig(sm):
    """LLMChat's real front door and leases over a MagicMock body."""
    from core.chat.chat import LLMChat
    llm = MagicMock()
    llm.session_manager = sm
    llm.system = None
    llm.closing = False
    llm._streams_lock = threading.Lock()
    llm._streams_by_id, llm._streams_by_chat = {}, {}
    for name in ('begin_stream', 'end_stream', '_vault_lease', '_register_stream'):
        setattr(llm, name, types.MethodType(getattr(LLMChat, name), llm))
    llm._vault_release = LLMChat._vault_release
    llm._live = LLMChat._live
    fm = llm.function_manager
    fm.snapshot_scopes.return_value = {}
    fm.snapshot_executors.return_value = {}
    fm.enabled_tools = []
    fm.last_dangling_toolset = None
    llm._build_base_messages.return_value = [{"role": "system", "content": "s"},
                                             {"role": "user", "content": "hi"}]
    llm.tool_engine.extract_function_call_from_text.return_value = None
    return llm


class Turn:
    """One turn on its own thread. The provider yields SAID[0], raises `mid`,
    then holds until `go` (or, with obey=True, until the engine is told to
    stop), then yields the rest. `late`: something the turn does after the
    hold, still inside the turn (a tool's write)."""

    def __init__(self, llm, chat=None, by_name=False, obey=True, late=None, text=TYPED):
        self.llm, self.sm = llm, llm.session_manager
        self.mid, self.go, self.done = threading.Event(), threading.Event(), threading.Event()
        self.obey, self.late, self.text = obey, late, text
        self.events, self.error, self.late_result = [], None, None
        self.stream, self.sid, self.chat = llm.begin_stream(chat, exclusive=True)
        if by_name:
            brain = self.sm.make_stream_session(self.chat)
            assert brain, "setup: the chat must be readable when the turn is accepted"
            brain["system_prompt"], brain["tools"] = "s", []
            self.stream.target_chat = self.chat
            self.stream.brain = brain
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _provider(self, *a, **k):
        yield {"type": "content", "text": SAID[0]}
        self.mid.set()
        while not self.go.is_set() and not (self.obey and self.stream.cancel_flag):
            time.sleep(0.005)
        if self.late is not None:
            try:
                self.late_result = self.late()
            except Exception as e:
                self.late_result = e
        for part in SAID[1:]:
            yield {"type": "content", "text": part}
        yield {"type": "done", "response": None}

    def _run(self):
        provider = MagicMock()
        provider.provider_name, provider.model = "test", "test-model"
        provider.chat_completion_stream.side_effect = self._provider
        self.llm._select_provider.return_value = ("test", provider, "")
        try:
            with patch('core.chat.chat_streaming.get_generation_params', return_value={}):
                for ev in self.stream.chat_stream(self.text):
                    self.events.append(ev)
        except Exception as e:
            self.error = e
        finally:
            self.llm.end_stream(self.sid, self.chat)
            self.done.set()

    def start(self, to_mid=True):
        self.thread.start()
        if to_mid:
            assert self.mid.wait(5), f"the turn never reached the provider: {self.error!r} {self.words()!r}"
        return self

    def finish(self, timeout=5):
        self.go.set()
        assert self.done.wait(timeout), "the turn never ended"
        return self

    def words(self):
        return ''.join(e.get('text', '') for e in self.events
                       if isinstance(e, dict) and e.get('type') == 'content')


@pytest.fixture
def rig(store, monkeypatch):
    """store: private chat 'secret', public chat 'desk' (the pointer is on
    it), vault open. Plaintext prompt store swapped for an empty one."""
    monkeypatch.setattr(pv, 'STOP_WAIT_S', 0.4)
    monkeypatch.setattr(pm, '_components', {'emotions': {}})
    monkeypatch.setattr(pm, '_monoliths', {})
    monkeypatch.setattr(pm, '_scenario_presets', {})
    monkeypatch.setattr(pm, 'save_components', lambda **kw: True)
    monkeypatch.setattr(pm, 'save_monoliths', lambda **kw: True)
    from core.event_bus import get_event_bus
    bus = get_event_bus()
    bus.clear_replay()
    yield _rig(store)
    bus.clear_replay()


# ── the scan ──

def _at_rest(sm):
    """Every byte of the chat database as it sits on disk, WAL included."""
    out = []
    for p in sorted(sm._db_path.parent.glob(sm._db_path.name + '*')):
        try:
            out.append(p.read_bytes())
        except FileNotFoundError:
            pass                    # sqlite drops its -wal / -shm when the last connection closes
    return b''.join(out)


def _rows(sm, name):
    with sm._get_connection() as conn:
        return [r[0] for r in conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = ? ORDER BY seq", (name,))]


def _replay_ring():
    from core.event_bus import get_event_bus
    return json.dumps(list(get_event_bus()._replay_buffer), default=str)


def assert_sealed_and_clean(sm, private=("secret",)):
    canary = CANARY.encode()
    assert not pv.vault_unlocked(), "the vault must be locked"
    assert pv.leases_held() == 0
    assert canary not in _at_rest(sm), "private words in plaintext in the chat database"
    for name in ("desk", "default"):
        assert not any(CANARY in r for r in _rows(sm, name)), f"private words in public chat '{name}'"
    assert sm.get_active_chat_name() not in private, "the pointer is still on a private chat"
    assert CANARY not in json.dumps(sm.current_chat.messages, default=str), \
        "private words in the pointer's in-memory buffer"
    assert CANARY not in json.dumps(sm.current_settings, default=str)
    assert CANARY not in json.dumps([pm._components, pm._monoliths, pm._scenario_presets])
    ring = _replay_ring()
    assert CANARY not in ring, "private words in the event replay ring"
    for name in private:
        assert f'"{name}"' not in ring, "a private chat's name in the event replay ring"


def _unlocked_rows(sm, name):
    """What the private chat really holds, read the honest way."""
    assert pv.unlock("changeme")[0]
    try:
        return json.dumps(sm.read_chat_messages(name), default=str)
    finally:
        assert pv.lock("check done")


# ── the scan is not blind ──

def test_the_scan_can_see_plaintext(rig):
    sm = rig.session_manager
    sm.add_user_message(TYPED)                       # the PUBLIC chat: plaintext by design
    assert CANARY.encode() in _at_rest(sm)
    with pytest.raises(AssertionError):
        pv.lock("user")
        assert_sealed_and_clean(sm)


def test_an_untouched_private_turn_is_ciphertext_at_rest(rig):
    """The baseline: no lock at all during the turn."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True).start().finish()
    assert t.error is None and CANARY in t.words()
    assert pv.lock("user")
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert TYPED in kept and "with me." in kept


# ── a clicked lock in the middle of a private reply ──

def test_lock_mid_reply_in_a_private_chat_running_by_name(rig):
    """A satellite, a phone call, a room seat: the turn the old lock could not
    see. It is stopped, what it had is saved encrypted, then the key drops."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True).start()
    assert pv.lock("user") is True
    assert t.done.wait(2) and t.error is None
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert TYPED in kept, "the person's words are kept"
    assert SAID[0].strip() in kept, "and what she had said so far"
    assert "with me." not in kept, "the rest was cut"


def test_lock_mid_reply_in_the_open_private_chat(rig):
    sm = rig.session_manager
    assert sm.set_active_chat("secret")
    t = Turn(rig).start()                            # typed: bound to the pointer
    assert t.stream.target_chat is None
    assert pv.lock("user") is True
    assert t.done.wait(2) and t.error is None
    assert sm.get_active_chat_name() in ("desk", "default"), "moved out to a public chat"
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert TYPED in kept and SAID[0].strip() in kept


def test_two_private_turns_and_a_public_one(rig):
    """The lock stops every private turn and leaves the public one alone."""
    sm = rig.session_manager
    sm.create_chat("also-secret")
    assert sm.set_active_chat("also-secret")
    assert sm.update_chat_settings({'private_chat': True}, expected_active="also-secret")
    assert sm.set_active_chat("desk")
    a = Turn(rig, "secret", by_name=True).start()
    b = Turn(rig, "also-secret", by_name=True).start()
    c = Turn(rig, "desk", by_name=True, text="public hello").start()
    assert pv.leases_held() == 2
    assert pv.lock("user") is True
    assert a.done.wait(2) and b.done.wait(2)
    assert not c.done.is_set() and not c.stream.cancel_flag, "a public turn is not the vault's to stop"
    c.finish()
    # the public turn answered with the provider's canned words: they are not private there
    assert not pv.vault_unlocked() and pv.leases_held() == 0
    assert CANARY not in _unlocked_rows(sm, "desk").replace(SAID[0], '')
    for name in ("secret", "also-secret"):
        assert TYPED in _unlocked_rows(sm, name)


# ── a turn that will not stop ──

def test_a_by_name_turn_that_ignores_the_stop(rig):
    """A tool that never returns: the lock waits its limit, seals anyway, and
    whatever the turn writes afterwards is refused, not written in plaintext."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True, obey=False).start()
    started = time.monotonic()
    assert pv.lock("user") is True
    assert 0.3 < time.monotonic() - started < 3, "it waited the limit, no longer"
    assert not pv.vault_unlocked() and not t.done.is_set()
    t.finish()                                       # now it writes the rest, keyless
    assert_sealed_and_clean(sm)
    assert TYPED in _unlocked_rows(sm, "secret"), "what was saved before the seal is still there"


def test_an_open_chat_turn_that_ignores_the_stop(rig):
    """The pointer cannot leave a chat with a live turn, so the private chat
    stays put until the turn ends. Its late writes are refused, and the
    pointer leaves the moment it does."""
    sm = rig.session_manager
    assert sm.set_active_chat("secret")
    t = Turn(rig, obey=False).start()
    assert pv.lock("user") is True
    assert not pv.vault_unlocked()
    assert CANARY.encode() not in _at_rest(sm), "sealed means sealed at rest, even with the turn alive"
    t.finish()
    assert sm.get_active_chat_name() in ("desk", "default")
    assert_sealed_and_clean(sm)


def test_a_stuck_turns_tool_cannot_write_private_words_into_the_prompt_store(rig):
    """Her prompt tools, called by a private turn after the key is gone."""
    from core.chat.function_manager import scope_private
    sm = rig.session_manager

    def late_tool():
        token = scope_private.set(True)
        try:
            with pv.reading(True):
                return (pc.save_component('emotions', 'aftermath', TYPED),
                        pc.save_prompt('aftermath', {'type': 'monolith', 'content': TYPED}))
        finally:
            scope_private.reset(token)

    t = Turn(rig, "secret", by_name=True, obey=False, late=late_tool).start()
    with patch.object(core.api_fastapi, 'get_system', return_value=types.SimpleNamespace(llm_chat=rig)):
        assert pv.lock("user") is True
        t.finish()
    assert [ok for ok, _ in t.late_result] == [False, False], t.late_result
    assert_sealed_and_clean(sm)


def test_a_stuck_turns_settings_write_does_not_land_anywhere_public(rig):
    """Custom Context written by a private turn after the seal (her own
    settings tools reach the same funnel)."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True, obey=False,
             late=lambda: sm.update_chat_settings({'custom_context': TYPED})).start()
    assert pv.lock("user") is True
    t.finish()
    assert t.late_result is not True, "a keyless settings write must not report success"
    assert_sealed_and_clean(sm)
    assert CANARY not in json.dumps(sm.read_chat_settings("desk"))


# ── the lock lands before the turn starts ──

def test_lock_between_accept_and_start_by_name(rig):
    """The message was accepted (the chat is held) but the engine has not run
    yet: a queue, a slow thread. The words are in flight, written nowhere."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True)            # accepted, not started
    assert pv.leases_held() == 1
    assert pv.lock("user") is True                   # waits its limit for a turn that is not running
    t.start(to_mid=False).finish()
    assert_sealed_and_clean(sm)
    assert CANARY not in _unlocked_rows(sm, "desk")


def test_lock_between_accept_and_start_in_the_open_chat(rig):
    sm = rig.session_manager
    assert sm.set_active_chat("secret")
    t = Turn(rig)                                    # typed in the private chat, accepted
    assert pv.lock("user") is True                   # evicts: the pointer is on 'desk' now
    assert sm.get_active_chat_name() in ("desk", "default")
    t.start(to_mid=False).finish()
    assert "🔒" in t.words(), "the turn is refused in words, not run in the public chat"
    assert_sealed_and_clean(sm)


def test_a_turn_sent_while_the_seal_is_under_way_is_refused(rig):
    """Enter pressed in the instant the lock is working: the door says busy
    and nothing is taken in."""
    from core.chat.chat import ChatBusy
    sm = rig.session_manager
    slow = Turn(rig, "secret", by_name=True, obey=False).start()
    refused = {}

    def send_during_seal():
        while not pv._sealing:
            time.sleep(0.002)
        try:
            rig.begin_stream("secret", exclusive=False)
            refused['r'] = False
        except ChatBusy:
            refused['r'] = True
    sender = threading.Thread(target=send_during_seal, daemon=True)
    sender.start()
    assert pv.lock("user") is True
    sender.join(2)
    assert refused == {'r': True}
    slow.finish()
    assert_sealed_and_clean(sm)


# ── the idle lock ──

def test_the_idle_lock_lets_a_private_reply_finish_then_seals(rig):
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True, obey=False).start()
    pv._last_activity = 0.0
    pv._on_idle()
    assert pv.vault_unlocked() and not t.stream.cancel_flag, "the idle lock waits"
    t.finish()
    _idle_seal_done()
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert TYPED in kept and "with me." in kept, "the whole reply was kept"


# ── delete vault under a turn ──

def test_delete_vault_under_a_turn_on_a_private_default(rig):
    """The purge brings `default` back as a PUBLIC chat. A turn that read the
    private one and would not stop must not save its window into it."""
    sm = rig.session_manager
    assert sm.set_active_chat("default")
    assert sm.update_chat_settings({'private_chat': True}, expected_active="default")
    sm.add_user_message(f"earlier private history {CANARY}")
    assert sm.set_active_chat("desk")
    t = Turn(rig, "default", by_name=True, obey=False).start()
    # a turn that will not stop: the vault locks and the delete WAITS (it used
    # to purge under the turn and lean on the purge mark alone)
    assert pv.destroy() == (False, 'busy')
    assert pv.vault_exists() and not pv.vault_unlocked() and sm.chat_exists("secret")
    t.finish()
    assert pv.destroy() == (True, '')
    assert sm.chat_exists("default") and not sm.chat_exists("secret")
    assert not pv.vault_exists()
    assert CANARY.encode() not in _at_rest(sm)
    assert _rows(sm, "default") == [], "the new public default starts empty and stays that way"
    assert CANARY not in _replay_ring()


# ── while typing ──

def test_a_sidebar_save_typed_for_the_private_chat_lands_nowhere_after_the_lock(rig):
    """Custom Context being typed when the vault locks: the save names the
    chat it was typed for, and the store refuses rather than write it onto
    the chat the pointer was moved to."""
    sm = rig.session_manager
    assert sm.set_active_chat("secret")
    assert pv.lock("user") is True                   # the pointer is on 'desk' now
    assert sm.update_chat_settings({'custom_context': TYPED}, expected_active="secret") is False
    assert sm.set_named_chat_settings("secret", {'custom_context': TYPED}) is False
    assert_sealed_and_clean(sm)
    assert CANARY not in json.dumps(sm.read_chat_settings("desk"))


def test_a_draft_typed_in_the_private_chat_is_not_taken_in_after_the_lock(rig):
    from core.routes.chat import _private_draft_elsewhere
    sm = rig.session_manager
    assert sm.set_active_chat("secret")
    assert pv.lock("user") is True
    assert _private_draft_elsewhere(sm, "secret", None) is True
    assert _private_draft_elsewhere(sm, "secret", "desk") is True


# ── a storm ──

@pytest.mark.parametrize("seed", [1, 7, 23, 101])
def test_lock_unlock_storm_against_private_writers(rig, monkeypatch, seed):
    """Hundreds of steps in a seeded random order: a private chat is taken,
    written to, let go, and the vault is locked and unlocked in between at
    every point of that. Whatever the order: a write either lands encrypted
    or is refused, nothing private is plaintext at rest right after any lock,
    and every write that reported success is in the chat exactly once.

    One thread on purpose. This store opens a connection per call, and many
    threads doing that at once is its known close hang (a separate, owed
    fix); the threaded cases are the scenarios above, two threads each."""
    import random
    from core.chat.chat import ChatBusy
    monkeypatch.setattr(pv, 'STOP_WAIT_S', 0.01)
    sm, rnd = rig.session_manager, random.Random(seed)
    held, written, n = [], 0, 0

    def take():
        try:
            held.append(rig.begin_stream("secret", exclusive=False)[1])
        except ChatBusy:
            pass

    def write():
        nonlocal written, n
        n += 1
        if sm.append_messages_to_chat("secret", [{"role": "user", "content": f"{TYPED} #{n}"}],
                                      max_wait_if_streaming=0.01):
            written += 1

    def let_go():
        if held:
            rig.end_stream(held.pop(rnd.randrange(len(held))), "secret")
            _idle_seal_done()        # the last one back may wake a waiting idle seal: one thread at a time

    def lock():
        pv.lock("user")
        assert not pv.vault_unlocked()
        assert CANARY.encode() not in _at_rest(sm), f"plaintext at rest right after a lock (seed {seed})"

    def idle():
        pv._last_activity = 0.0
        pv._on_idle()

    steps = [take, write, write, let_go, lock, idle, lambda: pv.unlock("changeme")]
    for _ in range(300):
        rnd.choice(steps)()
    while held:
        let_go()
    _idle_seal_done()                # a waiting idle seal runs on its own thread when the last turn ends
    pv.lock("user")
    assert written > 0, "the storm never wrote anything"
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert kept.count(CANARY) == written, "every write that reported success is there, once"


# ── what the web API hands out afterwards ──

def test_the_web_api_after_a_lock_in_the_middle_of_a_private_reply(rig, client, mock_system):
    """Every read door a browser tab uses, asked right after the seal: none
    may hand back the private words, list the private chat, or answer for it
    by name. The PUT a mid-typing sidebar would send is refused too."""
    sm = rig.session_manager
    mock_system.llm_chat.session_manager = sm
    mock_system.llm_chat.get_active_chat.side_effect = sm.get_active_chat_name
    assert sm.set_active_chat("secret")
    t = Turn(rig).start()
    assert pv.lock("user") is True and t.done.wait(2)
    c, csrf = client
    seen = {}
    for url in ('/api/chats', '/api/chats?stats=1', '/api/chats?slim=1', '/api/history',
                '/api/history?chat=secret', '/api/history/raw', '/api/chats/active',
                '/api/chats/secret/settings', '/api/chats/search?q=CANARY',
                '/api/chats/search?q=secret', '/api/chats/secret/export'):
        r = c.get(url)
        seen[url] = (r.status_code, r.text)
    blob = json.dumps(seen)
    assert CANARY not in blob, [u for u, (_, body) in seen.items() if CANARY in body]
    assert seen['/api/chats'][0] == 200
    assert 'secret' not in [x['name'] for x in json.loads(seen['/api/chats'][1])['chats']]
    for url in ('/api/history?chat=secret', '/api/chats/secret/settings', '/api/chats/secret/export'):
        assert seen[url][0] in (403, 404, 409), (url, seen[url][0])
    r = c.put('/api/chats/secret/settings', json={'settings': {'custom_context': TYPED}},
              headers={'X-CSRF-Token': csrf})
    assert r.status_code >= 400, "a save typed for the sealed chat must be refused"
    assert_sealed_and_clean(sm)
