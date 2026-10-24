"""Browser conversation drops: park + resume instead of exiting the mode.

A socket that drops without an Off parks its session for BROWSER_GRACE_S; the
tab's reconnect resumes it (no exit, no wakeword flap); a tab that never comes
back still ends the mode. Real BrowserConversationSource, mocked system/gate.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.conversation.manager as manager_mod
from core.conversation.manager import ConversationManager

from test_conversation_manager import _system_with_real_handoff


def _browser_mgr():
    system = _system_with_real_handoff()
    enter = system.enter_conversation_mode.side_effect
    system.enter_conversation_mode_external.side_effect = lambda acquire, source_label: enter(acquire)
    return system, ConversationManager(system, gate=MagicMock())


def _start(mgr):
    sent = []
    src = mgr.start_browser(sent.append)
    assert src is not None
    return src, sent


def test_park_keeps_mode_and_goes_quiet():
    system, mgr = _browser_mgr()
    src, sent = _start(mgr)
    assert mgr.park_browser(src) is True
    try:
        assert mgr.active is True                       # no exit -> no wakeword flap
        system.exit_conversation_mode.assert_not_called()
        n = len(sent)
        src.finish()
        src.feed_chunk({"audio_b64": "x"})
        assert len(sent) == n                           # nothing sent to the dead socket
        assert src._playback_done.is_set()              # an in-flight turn's wait() unblocks
    finally:
        mgr.stop()


def test_resume_reattaches_same_session():
    system, mgr = _browser_mgr()
    src, _ = _start(mgr)
    mgr.park_browser(src)
    sent2 = []
    assert mgr.resume_browser(sent2.append) is src
    assert mgr._park_timer is None                      # grace cancelled
    src.start()                                         # next turn re-arms the sink
    src.finish()
    assert sent2 == [{"type": "turn_audio_done"}]       # the new socket gets the traffic
    system.exit_conversation_mode.assert_not_called()
    mgr.stop()


def test_resume_without_park_returns_none():
    _, mgr = _browser_mgr()
    _start(mgr)
    assert mgr.resume_browser(lambda m: None) is None   # live session -> caller replaces it
    mgr.stop()


def test_grace_expiry_ends_mode(monkeypatch):
    monkeypatch.setattr(manager_mod, "BROWSER_GRACE_S", 0.05)
    _, mgr = _browser_mgr()
    src, _ = _start(mgr)
    mgr.park_browser(src)
    mgr._park_timer.join(1.0)
    assert mgr.active is False
    assert mgr.resume_browser(lambda m: None) is None


def test_stop_while_parked_cancels_grace():
    _, mgr = _browser_mgr()
    src, _ = _start(mgr)
    mgr.park_browser(src)
    timer = mgr._park_timer
    mgr.stop()                                          # Off over HTTP / local takeover
    assert mgr.active is False and mgr._parked is None
    assert timer.finished.is_set()                      # cancelled, won't fire later


def test_park_refused_when_session_replaced():
    system, mgr = _browser_mgr()
    src, _ = _start(mgr)
    system.conversation_session = MagicMock()           # another tab took over
    assert mgr.park_browser(src) is False
    assert mgr._parked is None


def test_expiry_never_kills_a_fresh_session(monkeypatch):
    """The expiry check + stop are atomic: a session started after the old one
    expired is never stopped by the old timer."""
    _, mgr = _browser_mgr()
    src, _ = _start(mgr)
    mgr.park_browser(src)
    mgr._park_timer.cancel()
    mgr._park_expired(src)                              # old session ends
    new, _ = _start(mgr)
    mgr._park_expired(src)                              # a late duplicate fire
    assert mgr.active is True and mgr.system.conversation_session is new
    mgr.stop()


def test_concurrent_resume_and_expiry_pick_one():
    for _ in range(20):
        _, mgr = _browser_mgr()
        src, _ = _start(mgr)
        mgr.park_browser(src)
        mgr._park_timer.cancel()
        got = []
        a = threading.Thread(target=lambda: got.append(mgr.resume_browser(lambda m: None)))
        b = threading.Thread(target=mgr._park_expired, args=(src,))
        a.start(); b.start(); a.join(); b.join()
        assert (got[0] is src) == mgr.active            # resumed and live, or expired and off
        mgr.stop()
