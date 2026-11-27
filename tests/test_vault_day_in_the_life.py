"""A day in the vault (Krem, 2026-11-26): ordinary use, with something
unexpected thrown in at an awkward moment.

Not attacks. A person in a private chat who edits, goes for pizza, talks while
a heartbeat fires, renames a chat, flips private on and off, and whose app
restarts, glitches or backs up underneath them. Each journey runs on the real
vault crypto, the real chat store on a temporary database and the real turn
engine (the rig from test_vault_seal_storm), plants CANARY in the private
chat's words, and then looks everywhere plaintext lives.

One more place than the storm pack first looked: THE LOG. Every journey runs
with every logger captured at DEBUG (the autouse check imported below), and
the canary must not be in a single line or traceback.
"""
import json
import shutil
import time

import pytest

import core.chat.history as hist
import core.prompt_vault as pv
from test_vault_isolation_hunt import store, _idle_seal_done   # noqa: F401
from test_vault_seal_storm import (   # noqa: F401
    rig, Turn, CANARY, TYPED, SAID, _at_rest, _rows, _replay_ring, _unlocked_rows, assert_sealed_and_clean,
    _log_lines, the_log_holds_no_private_words)


def _disk(sm):
    """The whole history folder as a crash would leave it."""
    root = sm._db_path.parent
    return b''.join(p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file())


@pytest.fixture
def hooks(rig):
    """The hook runner with the privacy resolver boot installs (sapphire.py):
    without it every chat reads as ordinary and the test proves nothing."""
    from core.hooks import hook_runner
    sm = rig.session_manager

    def resolve(name=None):
        if name is not None:
            s = sm.get_settings_for(name)
            return (name, True if s is None else bool(s.get('private_chat')))
        return (sm._effective_chat_name(), bool((sm.get_chat_settings() or {}).get('private_chat')))
    old = hook_runner._privacy_resolver
    hook_runner.set_privacy_resolver(resolve)
    yield hook_runner
    hook_runner.unregister_plugin('zz_flaky')
    hook_runner.unregister_plugin('zz_nosy')
    hook_runner.set_privacy_resolver(old)


def _private_and_open(sm, name="secret"):
    assert sm.set_active_chat(name)
    assert sm.get_chat_settings().get('private_chat') is True


# ── the app dies mid-reply ──

def test_a_crash_mid_reply_leaves_nothing_readable_and_the_next_boot_comes_up_sealed(rig, tmp_path):
    """kill -9 while she is answering in a private chat. What is on disk at
    that instant is all there will ever be: copy it, and it must hold no
    plaintext. Then boot a fresh store on the copy with no key (a restart is
    always locked) and look at what it serves."""
    sm = rig.session_manager
    _private_and_open(sm)
    t = Turn(rig).start()                                    # typed, pointer-bound, the reply is streaming
    crash = tmp_path / 'after-the-crash'
    shutil.copytree(sm._db_path.parent, crash)               # the disk at the instant of death
    t.finish()
    assert pv.lock("the test moves on")
    on_disk = b''.join(p.read_bytes() for p in sorted(crash.rglob('*')) if p.is_file())
    assert CANARY.encode() not in on_disk, "a crash mid-reply left private words in plaintext on disk"

    # the next boot: same files, no key
    from unittest.mock import patch
    defaults = {"prompt": "default"}
    with patch("core.chat.history.get_system_defaults", side_effect=lambda: dict(defaults)), \
         patch("core.chat.history.get_user_defaults", side_effect=lambda: dict(defaults)):
        booted = hist.ChatSessionManager(history_dir=str(crash))
        booted.evict_private_active()                        # boot's own step (sapphire.py)
        assert booted.get_active_chat_name() != 'secret', "the restart came up showing the private chat"
        assert 'secret' not in [c['name'] for c in booted.list_chat_files()]
        assert CANARY not in json.dumps(booted.get_messages_for_display(), default=str)
        assert booted.read_chat_settings('secret') is None
        assert booted.get_display_messages_for('secret') is None
        assert CANARY not in json.dumps(booted.current_settings, default=str)


def test_what_the_crashed_turn_had_written_is_there_after_the_next_unlock(rig):
    """The other half: a crash must not cost the person the words already
    written. The user row lands before the reply starts."""
    sm = rig.session_manager
    _private_and_open(sm)
    t = Turn(rig).start()
    with sm._get_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE chat_name = 'secret'").fetchone()[0]
    assert rows >= 1, "the person's message is on disk (encrypted) before she answers"
    t.finish()
    assert pv.lock("x")
    assert CANARY in _unlocked_rows(sm, "secret")


# ── private on, off, on ──

def test_making_a_chat_private_scrubs_its_old_words_from_the_database_file(rig):
    """Words typed while the chat was ORDINARY are plaintext on disk, by
    design. The moment it is made private they must be gone from the file's
    free pages and its WAL too, not only re-written as ciphertext."""
    sm = rig.session_manager
    assert sm.get_active_chat_name() == 'desk'
    sm.add_user_message(TYPED)
    sm.add_assistant_final(SAID[0])
    assert CANARY.encode() in _at_rest(sm), "setup: an ordinary chat's words are plaintext"
    assert sm.update_chat_settings({'private_chat': True}, expected_active='desk')
    assert CANARY.encode() not in _at_rest(sm), "the old plaintext is still in the database file"
    assert pv.lock("x")
    assert CANARY.encode() not in _disk(sm)
    assert CANARY in _unlocked_rows(sm, "desk"), "and nothing was lost"


def test_private_off_and_on_again_fast_ends_encrypted_and_whole(rig):
    sm = rig.session_manager
    _private_and_open(sm)
    sm.add_user_message(TYPED)
    sm.add_assistant_final(SAID[0])
    for _ in range(4):                                       # the unsure click, the double click
        assert sm.update_chat_settings({'private_chat': False}, expected_active='secret')
        assert sm.update_chat_settings({'private_chat': True}, expected_active='secret')
    assert CANARY.encode() not in _at_rest(sm)
    with sm._get_connection() as conn:
        vaulted = conn.execute("SELECT vaulted FROM chats WHERE name = 'secret'").fetchone()[0]
        plain = [r[0] for r in conn.execute(
            "SELECT message_json FROM chat_messages WHERE chat_name = 'secret'") if not str(r[0]).startswith('@enc1:')]
    assert vaulted == 1 and plain == [], "the flag and the rows must agree"
    assert pv.lock("x")
    assert_sealed_and_clean(sm)
    kept = _unlocked_rows(sm, "secret")
    assert kept.count(CANARY) >= 2, "both rows survived eight flips"


def test_a_turn_between_two_flips_writes_in_the_state_the_chat_ends_in(rig):
    """Private, a reply starts, the person flips it public and private again
    before the reply lands. Whatever the reply writes must be encrypted."""
    sm = rig.session_manager
    _private_and_open(sm)
    t = Turn(rig, obey=False).start()
    sm.update_chat_settings({'private_chat': False}, expected_active='secret')
    sm.update_chat_settings({'private_chat': True}, expected_active='secret')
    t.finish()
    assert CANARY.encode() not in _at_rest(sm)
    assert pv.lock("x")
    assert_sealed_and_clean(sm)


# ── a rename while she is answering ──

def test_a_rename_mid_reply_never_leaves_rows_under_the_old_name_in_plaintext(rig):
    sm = rig.session_manager
    _private_and_open(sm)
    t = Turn(rig, obey=False).start()
    ok, new = sm.rename_chat('secret', 'diary')
    t.finish()
    assert CANARY.encode() not in _at_rest(sm), "the late rows were written in plaintext"
    with sm._get_connection() as conn:
        owners = {r[0] for r in conn.execute("SELECT DISTINCT chat_name FROM chat_messages")}
        chats = {r[0]: r[1] for r in conn.execute("SELECT name, vaulted FROM chats")}
    final = new if ok else 'secret'
    assert chats.get(final) == 1, "the chat is still a vaulted one under its final name"
    assert owners <= set(chats), f"rows left under a name that is no chat: {owners - set(chats)}"
    if ok:
        assert 'secret' not in chats, "the rename left a second chat behind under the old name"
    assert pv.lock("x")
    assert_sealed_and_clean(sm, private=("secret", "diary"))


def test_a_rename_by_name_lane_mid_reply(rig):
    """The same, for a turn that is not the open chat's (a task, a room)."""
    sm = rig.session_manager
    t = Turn(rig, "secret", by_name=True, obey=False).start()
    sm.rename_chat('secret', 'diary')
    t.finish()
    assert CANARY.encode() not in _at_rest(sm)
    for name in ('desk', 'default'):
        assert not any(CANARY in r for r in _rows(sm, name))
    with sm._get_connection() as conn:
        chats = {r[0]: r[1] for r in conn.execute("SELECT name, vaulted FROM chats")}
        owners = {r[0] for r in conn.execute("SELECT DISTINCT chat_name FROM chat_messages")}
    assert owners <= set(chats), f"rows left under a name that is no chat: {owners - set(chats)}"
    assert all(v == 1 for n, v in chats.items() if n in ('secret', 'diary'))
    assert pv.lock("x")
    assert_sealed_and_clean(sm, private=("secret", "diary"))


# ── a glitch before Enter ──

def _corrupt_settings(sm, name, value):
    with sm._get_connection() as conn:
        conn.execute("UPDATE chats SET settings = ? WHERE name = ?", (value, name))
        conn.commit()


@pytest.mark.parametrize("damage", ["@enc1:not-a-real-ciphertext", "{not json", ""])
def test_a_private_chat_whose_settings_went_bad_is_not_treated_as_an_ordinary_chat(rig, damage):
    """The chat's settings row is unreadable when the person presses Enter.
    Unreadable must never be read as "not private": no lane may run it as an
    ordinary chat, and nothing typed for it may land in plaintext."""
    sm = rig.session_manager
    assert sm.get_active_chat_name() == 'desk'
    _corrupt_settings(sm, 'secret', damage)
    s = sm.read_chat_settings('secret')
    assert s is None or s.get('private_chat') is True, f"a damaged private chat read as ordinary: {s}"
    g = sm.get_settings_for('secret')
    assert g is None or g.get('private_chat') is True
    brain = sm.make_stream_session('secret')
    if brain is not None:
        assert (brain.get('settings') or {}).get('private_chat') is True, "a by-name turn would run it as ordinary"
    # the row is still a vaulted one: whatever a late writer appends is encrypted or refused
    sm.append_messages_to_chat('secret', [{"role": "user", "content": TYPED}])
    assert CANARY.encode() not in _at_rest(sm), "words typed for the damaged chat were stored in plaintext"
    assert pv.lock("x")
    assert CANARY.encode() not in _disk(sm)


def test_an_encrypted_chat_is_private_whatever_its_settings_say(rig):
    """THE finding of this run. The privacy answer came from the settings
    alone: a settings value that went blank made an encrypted chat read as an
    ordinary one, to every gate that asks."""
    sm = rig.session_manager
    _corrupt_settings(sm, 'secret', '')
    for read in (sm.read_chat_settings, sm.get_settings_for):
        s = read('secret')
        assert s is not None and s.get('private_chat') is True, read.__name__
    listed = {c['name']: c for c in sm.list_chat_files()}
    assert listed['secret'].get('private_chat') is True
    assert sm.set_active_chat('secret')
    assert sm.get_chat_settings().get('private_chat') is True, "the open chat's own settings say so too"
    assert sm.set_active_chat('desk')
    assert pv.lock("x")
    assert 'secret' not in [c['name'] for c in sm.list_chat_files()], "and it hides with the rest when the vault locks"
    assert sm.read_chat_settings('secret') is None


def test_a_private_chat_whose_message_row_went_bad_still_opens_or_says_why(rig):
    sm = rig.session_manager
    _private_and_open(sm)
    sm.add_user_message(TYPED)
    sm.add_assistant_final(SAID[0])
    assert sm.set_active_chat('desk')
    with sm._get_connection() as conn:
        conn.execute("UPDATE chat_messages SET message_json = '@enc1:garbage' WHERE chat_name = 'secret' "
                     "AND seq = (SELECT MIN(seq) FROM chat_messages WHERE chat_name = 'secret')")
        conn.commit()
    opened = sm.set_active_chat('secret')
    shown = json.dumps(sm.get_messages_for_display(), default=str)
    assert 'garbage' not in shown, "raw ciphertext must never be shown as a message"
    if opened:
        assert sm.is_chat_degraded('secret'), "a chat with an unreadable row says so, and is read-only"
    assert pv.lock("x")
    assert_sealed_and_clean(sm)


# ── a plugin fails mid-hook ──

def test_a_plugin_that_fails_mid_hook_does_not_put_the_words_in_the_log(rig, hooks):
    """A privacy-aware plugin gets the turn's words, then raises, and its
    exception quotes them. The turn must go on, and the log must not carry
    the quote (the autouse check reads every logger and every traceback)."""
    seen = []

    def bad_hook(event):
        seen.append(getattr(event, 'input', None))
        raise ValueError(f"could not parse: {getattr(event, 'input', '')} {CANARY}")
    hooks.register('pre_chat', bad_hook, plugin_name='zz_flaky')
    hooks.register('post_chat', bad_hook, plugin_name='zz_flaky')
    hooks.mark_privacy_aware('zz_flaky', True)
    sm = rig.session_manager
    _private_and_open(sm)
    t = Turn(rig).start().finish()
    assert t.error is None and CANARY in t.words(), "one bad plugin must not stop the reply"
    assert len(seen) >= 2, "setup: the hook really ran and raised (before and after the reply)"
    assert pv.lock("x")
    assert_sealed_and_clean(sm)


def test_a_failing_hook_on_an_ordinary_chat_still_logs_what_went_wrong(rig, hooks, caplog):
    """The other half: a plugin author debugging on an ordinary chat keeps
    the exception's words."""
    def bad_hook(event):
        raise ValueError("the plugin's own explanation")
    hooks.register('pre_chat', bad_hook, plugin_name='zz_flaky')
    # by name: a typed turn in an ordinary chat is refused while the vault is open
    Turn(rig, "desk", by_name=True, text="an ordinary question").start().finish()
    assert any("the plugin's own explanation" in t for _n, _l, t in _log_lines(caplog))


def test_a_plugin_that_is_not_privacy_aware_is_never_handed_a_private_turn(rig, hooks):
    got = []
    for name in ('pre_chat', 'post_chat', 'post_llm', 'prompt_inject', 'pre_execute', 'post_execute'):
        hooks.register(name, lambda event, n=name: got.append(n), plugin_name='zz_nosy')
    sm = rig.session_manager
    _private_and_open(sm)
    Turn(rig).start().finish()
    assert got == [], f"a plugin without the privacy flag received a private turn: {got}"
    assert pv.lock("x")


# ── talking when the heartbeat fires ──

def test_a_heartbeat_on_the_same_private_chat_waits_and_a_lock_stops_both_lanes(rig):
    from core.chat.chat import ChatBusy
    sm = rig.session_manager
    _private_and_open(sm)
    typed = Turn(rig).start()                               # the person's reply is streaming
    with pytest.raises(ChatBusy):
        rig.begin_stream("secret", exclusive=True)          # the heartbeat fires at the same chat
    assert pv.leases_held() == 1, "the refused heartbeat holds nothing open"
    assert pv.lock("user") is True                          # and now the padlock
    typed.finish()
    assert_sealed_and_clean(sm)


def test_a_heartbeat_on_another_private_chat_while_she_talks(rig):
    sm = rig.session_manager
    assert sm.create_chat("journal")
    assert sm.set_named_chat_settings("journal", {'private_chat': True})
    _private_and_open(sm)
    typed = Turn(rig).start()
    beat = Turn(rig, "journal", by_name=True).start()       # a task's turn on the other private chat
    assert pv.leases_held() == 2
    assert pv.lock("user") is True
    typed.finish()
    beat.finish()
    assert_sealed_and_clean(sm, private=("secret", "journal"))
    for name in ("desk", "default"):
        assert not any(CANARY in r for r in _rows(sm, name))


# ── the pizza ──

def _idle(seconds_ago, monkeypatch, timeout=1800.0):
    monkeypatch.setattr(pv, '_idle_seconds', lambda: timeout)
    pv._last_activity = time.monotonic() - seconds_ago


@pytest.mark.parametrize("away, locked", [(29 * 60 + 50, False), (30 * 60, True), (30 * 60 + 10, True)])
def test_back_from_pizza_at_the_edge_of_the_timer(rig, monkeypatch, away, locked):
    """Thirty minutes idle. The timer fires and looks at when she last did
    something: ten seconds early it re-arms for the rest, on time it seals."""
    sm = rig.session_manager
    _private_and_open(sm)
    sm.add_user_message(TYPED)
    _idle(away, monkeypatch)
    pv._on_idle()
    assert (not pv.vault_unlocked()) is locked
    if locked:
        assert_sealed_and_clean(sm)
    else:
        assert pv._timer is not None, "the clock is running again for the time that is left"
        assert sm.get_active_chat_name() == 'secret'
    pv.stop()


def test_she_presses_enter_in_the_same_instant_the_timer_fires(rig, monkeypatch):
    """Both orders. Enter first: the turn holds the vault and the seal waits
    for it. Timer first: the vault is sealed and the turn meets a locked chat;
    her words must not land anywhere in plaintext."""
    sm = rig.session_manager
    _private_and_open(sm)
    # Enter first
    t = Turn(rig).start()
    _idle(1801, monkeypatch)
    pv._on_idle()
    assert pv.vault_unlocked() and pv._pending, "the idle seal waits for the reply in progress"
    t.finish()
    _idle_seal_done()                                        # the waiting seal runs on its own thread: let it land
    assert not pv.vault_unlocked(), "and seals when it has ended"
    assert_sealed_and_clean(sm)
    assert CANARY in _unlocked_rows(sm, "secret"), "the whole reply was kept"
    # timer first: she presses Enter into a vault that just sealed
    assert not pv.vault_unlocked()
    try:
        late = Turn(rig, "secret", by_name=False)
        late.start(to_mid=False)
        late.go.set()
        late.done.wait(5)
    except Exception:
        pass                                                 # refused at the door is a fine answer
    assert CANARY.encode() not in _at_rest(sm)
    for name in ("desk", "default"):
        assert not any(CANARY in r for r in _rows(sm, name)), "words typed for the private chat landed in an ordinary one"


# ── a picture described while the chat was ordinary ──

def test_making_a_chat_private_encrypts_its_picture_captions_and_set_aside_rows(rig):
    """The flip took a picture's pixels and left its caption (the image put
    into words) in plaintext, at rest and in every backup after. Rows a
    repair had set aside were left too (two scouts, 2026-11-26)."""
    sm = rig.session_manager
    assert sm.get_active_chat_name() == 'desk'
    assert sm.save_tool_image('aaaabbbbcccc', b'pixels', 'image/png', chat_name='desk')
    assert sm.set_image_caption('aaaabbbbcccc', f"a photo of {CANARY}")
    with sm._get_connection() as conn:
        conn.execute("INSERT INTO chat_messages_quarantine (chat_name, orig_seq, role, message_json, reason, quarantined_at) "
                     "VALUES ('desk', 1, 'user', ?, 'test', '2026-11-26')", (json.dumps({'role': 'user', 'content': TYPED}),))
        conn.commit()
    assert CANARY.encode() in _at_rest(sm), "setup: plaintext while the chat is ordinary"
    assert sm.update_chat_settings({'private_chat': True}, expected_active='desk')
    assert CANARY.encode() not in _at_rest(sm), "a caption or a set-aside row stayed in plaintext"
    assert CANARY in sm.image_caption('aaaabbbbcccc'), "and the caption still reads, to the chat that owns it"
    # and back: making it ordinary again returns them, not ciphertext in an ordinary chat
    assert sm.update_chat_settings({'private_chat': False}, expected_active='desk')
    with sm._get_connection() as conn:
        cap = conn.execute("SELECT caption FROM tool_images WHERE id = 'aaaabbbbcccc'").fetchone()[0]
        q = conn.execute("SELECT message_json FROM chat_messages_quarantine WHERE chat_name = 'desk'").fetchone()[0]
    assert not str(cap).startswith('@enc1:') and CANARY in cap
    assert not str(q).startswith('@enc1:') and CANARY in q


# ── a backup kicks in while the vault is open ──

def test_a_backup_taken_mid_reply_with_the_vault_open_holds_no_private_words(rig, tmp_path, monkeypatch):
    """The nightly backup fires while she is answering in a private chat and
    the vault is unlocked. The real backup code, on this journey's own tree:
    the archive must hold the chat as ciphertext, the vault file beside it
    (one without the other cannot be restored), a database that opens, and
    not one private word. The turn must come through it untouched."""
    import sqlite3
    import tarfile
    import threading
    from unittest.mock import patch
    import core.backup as B
    from core.backup import Backup
    sm = rig.session_manager
    _private_and_open(sm)
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.base_dir = tmp_path.parent
    b.user_dir = tmp_path                                    # hist/ (the chat store) and the vault file live here
    b.backup_dir = tmp_path.parent / f"{tmp_path.name}-backups"
    b.backup_dir.mkdir(parents=True, exist_ok=True)
    b._stop_event = None
    b._backup_op_lock = threading.Lock()

    class Cfg:
        BASE_DIR = tmp_path.parent
        BACKUPS_ENABLED = True
        BACKUPS_EXCLUDE_PATTERNS = []
        BACKUPS_KEEP_DAILY, BACKUPS_KEEP_WEEKLY, BACKUPS_KEEP_MONTHLY, BACKUPS_HOUR = 7, 0, 0, 3
    monkeypatch.setattr(B, "config", Cfg)

    t = Turn(rig, obey=False).start()                        # the reply is streaming: the user row is written
    name = b.create_backup("manual", encrypt=False)          # a plain archive, so every byte can be read
    assert name, "the backup did not complete while a private turn was writing"
    t.finish()
    assert t.error is None and CANARY in t.words(), "the backup must not disturb the reply"

    archive = b.backup_dir / name
    out = tmp_path.parent / f"{tmp_path.name}-unpacked"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        try:
            tar.extractall(out, filter="data")
        except TypeError:
            tar.extractall(out)
    blob = b''.join(p.read_bytes() for p in sorted(out.rglob('*')) if p.is_file())
    assert CANARY.encode() not in blob, "private words in plaintext inside the backup archive"
    assert CANARY.encode() not in archive.read_bytes()
    assert any(n.endswith('prompt_vault.enc') for n in names), "the vault file is not in the archive: its chats could never be opened"
    dbs = [p for p in out.rglob('*.db')]
    assert dbs, "the chat database is not in the archive"
    assert not [n for n in names if n.endswith(('-wal', '-shm'))], "a live WAL in the archive is a torn database"
    rows = None
    for db in dbs:                                           # the chat store is the one with chat_messages
        con = sqlite3.connect(str(db))
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == 'ok', db.name
            if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'chat_messages'").fetchone():
                rows = [r[0] for r in con.execute("SELECT message_json FROM chat_messages WHERE chat_name = 'secret'")]
        finally:
            con.close()
    assert rows and all(str(r).startswith('@enc1:') for r in rows), "the private chat's rows ride the archive as ciphertext"
    assert pv.lock("x")
    assert_sealed_and_clean(sm)
