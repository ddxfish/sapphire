"""Conversation manager + VAD gate tests (v3 Rollout 2b).

Gate + source are injected, so no silero model load and no real mic — we assert
the manager wires the front-door through the fail-safe handoff and exits cleanly.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversation.manager import ConversationManager
from core.conversation.vad import SpeechGate


# ── SpeechGate ──────────────────────────────────────────────────────────────
def test_speech_gate_threshold():
    # Hysteresis (2026-07-15): enter at `threshold`, STAY in speech down to
    # exit_threshold (default threshold - 0.15). Mid-word dips don't flicker.
    probs = iter([0.9, 0.1, 0.6, 0.49, 0.2])
    g = SpeechGate(threshold=0.5, score_fn=lambda chunk: next(probs))
    assert g.is_speech(b"x") is True       # 0.9 >= 0.5 (enter)
    assert g.is_speech(b"x") is False      # 0.1 < 0.35 (exit)
    assert g.is_speech(b"x") is True       # 0.6 >= 0.5 (enter again)
    assert g.is_speech(b"x") is True       # 0.49 >= 0.35 — hysteresis holds
    assert g.is_speech(b"x") is False      # 0.2 < 0.35 — real silence releases


def test_speech_gate_hysteresis_never_enters_below_threshold():
    # From silence, a score in the hysteresis band must NOT start speech.
    probs = iter([0.45, 0.45])
    g = SpeechGate(threshold=0.5, score_fn=lambda chunk: next(probs))
    assert g.is_speech(b"x") is False      # 0.45 < 0.5 — band only applies in-speech
    assert g.is_speech(b"x") is False


def test_speech_gate_reset_clears_hysteresis():
    probs = iter([0.9, 0.45])
    g = SpeechGate(threshold=0.5, score_fn=lambda chunk: next(probs))
    assert g.is_speech(b"x") is True       # in speech
    g.reset()
    assert g.is_speech(b"x") is False      # 0.45 judged as fresh entry, not held


def test_speech_gate_swallows_scorer_error():
    def boom(chunk):
        raise RuntimeError("vad blew up")
    g = SpeechGate(threshold=0.5, score_fn=boom)
    assert g.is_speech(b"x") is False      # degrades to "not speech", never raises


# ── ConversationManager ─────────────────────────────────────────────────────
def _system_with_real_handoff():
    """Mock system whose enter/exit run the acquire/close like the real handoff,
    so we can prove the manager drives them end to end."""
    system = MagicMock()
    system.conversation_mode_enabled = False

    def enter(acquire):
        try:
            sess = acquire()
        except Exception:
            return False
        if sess is None:
            return False
        system.conversation_session = sess
        system.conversation_mode_enabled = True
        return True
    system.enter_conversation_mode.side_effect = enter

    def exit_():
        sess = getattr(system, "conversation_session", None)
        if sess is not None and hasattr(sess, "close"):
            sess.close()
        system.conversation_session = None
        system.conversation_mode_enabled = False
    system.exit_conversation_mode.side_effect = exit_
    return system


def test_manager_start_local_enters_via_handoff():
    system = _system_with_real_handoff()
    source = MagicMock()
    made = []
    mgr = ConversationManager(system, gate=MagicMock(),
                              source_factory=lambda d, g: (made.append((d, g)) or source))
    assert mgr.start_local() is True
    assert mgr.active is True
    assert len(made) == 1                  # the front-door source was built
    assert made[0][0] is mgr.driver        # driver handed to the source


def test_manager_start_when_active_is_noop():
    system = _system_with_real_handoff()
    system.conversation_mode_enabled = True
    calls = []
    mgr = ConversationManager(system, gate=MagicMock(),
                              source_factory=lambda d, g: calls.append(1))
    assert mgr.start_local() is True
    assert calls == []                     # didn't re-acquire


def test_manager_stop_exits_and_closes_source():
    system = _system_with_real_handoff()
    source = MagicMock()
    mgr = ConversationManager(system, gate=MagicMock(), source_factory=lambda d, g: source)
    mgr.start_local()
    mgr.stop()
    assert mgr.active is False
    source.close.assert_called_once()      # source torn down on exit


def test_manager_source_failure_keeps_wakeword():
    """If the front-door raises, the handoff returns False and mode stays off."""
    system = _system_with_real_handoff()

    def boom(d, g):
        raise RuntimeError("mic busy / WASAPI")
    mgr = ConversationManager(system, gate=MagicMock(), source_factory=boom)
    assert mgr.start_local() is False
    assert mgr.active is False


# ── External sessions (Phase II — phone calls, N concurrent) ────────────────
def _ctor(built=None):
    def ctor(driver, gate):
        src = MagicMock()
        if built is not None:
            built.append((driver, src))
        return src
    return ctor


def test_external_sessions_run_concurrently_with_own_drivers():
    system = _system_with_real_handoff()
    mgr = ConversationManager(system, gate=MagicMock())
    built = []
    a = mgr.start_external(_ctor(built), chat_name="call_a", session_id="a")
    b = mgr.start_external(_ctor(built), chat_name="call_b", session_id="b")
    assert a is not None and b is not None
    assert len(mgr.external) == 2
    da, db = built[0][0], built[1][0]
    assert da is not db                      # one driver PER call
    assert da._chat_name == "call_a" and db._chat_name == "call_b"
    assert mgr.driver is None                # operator slot untouched
    assert system.conversation_mode_enabled is False   # operator mode untouched
    system.enter_conversation_mode_external.assert_not_called()


def test_external_slot_cap_refuses_over_capacity():
    system = _system_with_real_handoff()
    mgr = ConversationManager(system, gate=MagicMock())
    assert mgr.start_external(_ctor(), session_id="a") is not None
    assert mgr.start_external(_ctor(), session_id="b") is not None
    assert mgr.start_external(_ctor(), session_id="c") is None   # cap default 2
    assert len(mgr.external) == 2


def test_external_same_chat_refused_serialize_per_chat():
    """A2 (2026-07-09): two live sessions on ONE chat would each seed from a
    start-of-session snapshot and whole-column overwrite, silently eating the
    other's turns. A second session targeting an already-live chat is refused
    (the caller rejects it like a busy line); a DIFFERENT chat still gets in."""
    system = _system_with_real_handoff()
    mgr = ConversationManager(system, gate=MagicMock())
    a = mgr.start_external(_ctor(), chat_name="shared", session_id="a")
    b = mgr.start_external(_ctor(), chat_name="shared", session_id="b")
    assert a is not None
    assert b is None                              # same chat → refused
    assert len(mgr.external) == 1
    c = mgr.start_external(_ctor(), chat_name="other", session_id="c")
    assert c is not None                          # per-chat, not a global lock
    assert len(mgr.external) == 2


def test_external_chatless_sessions_not_serialized():
    """chat_name=None sessions aren't collision-checked — only truthy names
    serialize (guards the `if chat_name` in the A2 check)."""
    system = _system_with_real_handoff()
    mgr = ConversationManager(system, gate=MagicMock())
    a = mgr.start_external(_ctor(), session_id="a")   # chat_name None
    b = mgr.start_external(_ctor(), session_id="b")   # chat_name None
    assert a is not None and b is not None            # both allowed (not refused on None)
    assert len(mgr.external) == 2


def test_stop_external_ends_one_leaves_other():
    system = _system_with_real_handoff()
    mgr = ConversationManager(system, gate=MagicMock())
    built = []
    mgr.start_external(_ctor(built), session_id="a")
    mgr.start_external(_ctor(built), session_id="b")
    mgr.stop_external("a")
    assert set(mgr.external) == {"b"}
    built[0][1].close.assert_called_once()   # a's source closed
    built[1][1].close.assert_not_called()    # b untouched
    mgr.stop_external("a")                   # idempotent
    mgr.stop_external("b")
    assert mgr.external == {}


def test_operator_stop_leaves_external_sessions_alive():
    system = _system_with_real_handoff()
    source = MagicMock()
    mgr = ConversationManager(system, gate=MagicMock(), source_factory=lambda d, g: source)
    built = []
    mgr.start_external(_ctor(built), session_id="call")
    mgr.start_local()
    mgr.stop()                               # operator exits their conversation
    assert mgr.active is False
    assert set(mgr.external) == {"call"}     # the phone call never broke stride
    built[0][1].close.assert_not_called()
