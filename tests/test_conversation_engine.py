"""Conversation turn-state machine tests (v3 Rollout 2a).

Pure logic, deterministic — frames carry their duration in their length, so no
wall clock. Covers: normal turn dispatch, blip rejection (endpointing tuning),
barge-in, brief-noise rejection, and the turn_finished -> IDLE return.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversation.engine import ConversationEngine, IDLE, USER_SPEAKING, RESPONDING

SR = 16000


def frame(ms, speech):
    """A (pcm_bytes, is_speech) frame of `ms` milliseconds. Content is irrelevant
    (engine only reads length), so use silence-valued int16 samples."""
    samples = int(SR * ms / 1000)
    return (b"\x00\x00" * samples, speech)


def _engine(**kw):
    turns, barges = [], []
    defaults = dict(endpoint_silence_ms=700, min_speech_ms=200, barge_hold_ms=90)
    defaults.update(kw)
    e = ConversationEngine(on_turn=turns.append,
                           on_barge_in=lambda: barges.append(1), **defaults)
    return e, turns, barges


def test_normal_turn_dispatches_once():
    e, turns, barges = _engine()
    for _ in range(5):                       # 500ms speech
        e.push_frame(*frame(100, True))
    assert e.state == USER_SPEAKING
    for _ in range(7):                       # 700ms silence -> endpoint
        e.push_frame(*frame(100, False))
    assert len(turns) == 1
    assert len(turns[0]) // 2 / SR * 1000 >= 1100   # captured speech+trailing silence
    assert e.state == RESPONDING
    assert barges == []


def test_short_blip_is_discarded():
    e, turns, _ = _engine(endpoint_silence_ms=300, min_speech_ms=200)
    e.push_frame(*frame(100, True))          # only 100ms speech (< 200 min)
    for _ in range(3):                       # 300ms silence -> endpoint
        e.push_frame(*frame(100, False))
    assert turns == []                        # rejected as a blip
    assert e.state == IDLE


def test_barge_in_during_responding():
    e, turns, barges = _engine(endpoint_silence_ms=300, min_speech_ms=100)
    e.push_frame(*frame(150, True))
    for _ in range(3):
        e.push_frame(*frame(100, False))      # -> RESPONDING
    assert e.state == RESPONDING and len(turns) == 1
    e.arm_barge()                             # prose flowing — driver armed us
    e.push_frame(*frame(100, True))           # 100ms speech >= 90ms barge_hold
    assert barges == [1]
    assert e.state == USER_SPEAKING           # now capturing the barge-in utterance


def test_brief_noise_does_not_barge():
    e, turns, barges = _engine(endpoint_silence_ms=300, min_speech_ms=100)
    e.push_frame(*frame(150, True))
    for _ in range(3):
        e.push_frame(*frame(100, False))
    assert e.state == RESPONDING
    e.arm_barge()
    e.push_frame(*frame(50, True))            # 50ms blip (< 90ms barge_hold)
    e.push_frame(*frame(100, False))          # silence resets barge timer
    assert barges == []
    assert e.state == RESPONDING


def test_no_barge_while_disarmed_preproc():
    """Speech during LLM preproc/thinking (RESPONDING but not armed) is ignored —
    the friend-cadence bug: talk-pause-talk before prose ever flowed cancelled
    every turn. Arming restarts the hold timer fresh."""
    e, turns, barges = _engine(endpoint_silence_ms=300, min_speech_ms=100)
    e.push_frame(*frame(150, True))
    for _ in range(3):
        e.push_frame(*frame(100, False))      # -> RESPONDING, disarmed
    assert e.state == RESPONDING
    for _ in range(5):
        e.push_frame(*frame(100, True))       # 500ms of talking over her silence
    assert barges == []                        # nothing to interrupt — ignored
    assert e.state == RESPONDING
    e.arm_barge()                              # prose starts
    e.push_frame(*frame(50, True))             # accumulation starts FRESH (50 < 90)
    assert barges == []
    e.push_frame(*frame(50, True))             # 100ms total since arming
    assert barges == [1]
    assert e.state == USER_SPEAKING


def test_turn_finished_returns_to_idle():
    e, turns, _ = _engine(endpoint_silence_ms=300, min_speech_ms=100)
    e.push_frame(*frame(150, True))
    for _ in range(3):
        e.push_frame(*frame(100, False))
    assert e.state == RESPONDING
    e.turn_finished()
    assert e.state == IDLE


def test_max_utterance_force_endpoints():
    e, turns, _ = _engine(endpoint_silence_ms=10000, min_speech_ms=100, max_utterance_ms=1000)
    for _ in range(10):                       # exactly 1000ms continuous speech -> hits cap
        e.push_frame(*frame(100, True))
    assert len(turns) == 1                     # capped -> forced endpoint
    assert e.state == RESPONDING
    # (talking PAST the cap would correctly read as a barge-in on the response —
    #  covered by test_barge_in_during_responding.)
