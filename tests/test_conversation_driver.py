"""Conversation streaming-turn driver tests (v3 Rollout 2b).

The driver drives chat_stream: content -> event bus, tts_chunk -> sink. We inject a
fake transcribe, a fake stream (scripted events), and a fake sink, and run turn
dispatch synchronously, so we assert the real wiring without whisper/LLM/audio.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversation.driver import ConversationDriver, match_start_word
from core.conversation.engine import IDLE, RESPONDING, USER_SPEAKING
from core.event_bus import Events

SR = 16000


# ── start-word gate ──
def test_start_word_off_passes_through():
    assert match_start_word("what time is it", "") == "what time is it"
    assert match_start_word("what time is it", "   ") == "what time is it"


def test_start_word_exact_prefix_strips():
    assert match_start_word("hey sapphire what time is it", "hey sapphire") == "what time is it"
    assert match_start_word("Hey Sapphire, what's up?", "hey sapphire") == "what's up?"


def test_start_word_fuzzy_near_miss():
    assert match_start_word("hey sapphir what time", "hey sapphire", 0.7) == "what time"


def test_start_word_fuzzy_word_split():
    # STT split "sapphire" -> "staff fire": window matching strips all 3 leading words
    assert match_start_word("hey staff fire turn on lights", "hey sapphire", 0.5) == "turn on lights"


def test_start_word_no_match_gates():
    assert match_start_word("random youtube lyrics here", "hey sapphire") is None


def test_start_word_only_returns_empty():
    assert match_start_word("hey sapphire", "hey sapphire") == ""


def test_start_word_multiple_phrases():
    assert match_start_word("sapphire what time", "hey sapphire, sapphire") == "what time"
    assert match_start_word("nope nothing matches", "hey sapphire, sapphire") is None


def frame(ms, speech):
    samples = int(SR * ms / 1000)
    return (b"\x00\x00" * samples, speech)


def _driver(transcript="hello sapphire", events=None, sink=None):
    system = MagicMock()
    if events is None:
        events = [
            {"type": "content", "text": "Hi "},
            {"type": "content", "text": "there"},
            {"type": "tts_chunk", "audio_b64": "x", "stream_id": "s"},
            {"type": "done"},
        ]
    fake_stream = MagicMock()
    fake_stream.cancel_flag = False
    fake_stream.chat_stream.return_value = iter(events)
    system.llm_chat.begin_stream.return_value = (fake_stream, "sid", "chat")
    if sink is None:
        sink = MagicMock()
        sink._worker = None
    d = ConversationDriver(system, transcribe_fn=lambda p: transcript,
                           sink_factory=lambda: sink,
                           endpoint_silence_ms=300, min_speech_ms=100, barge_hold_ms=90)
    d._spawn = lambda target, *a: target(*a)   # run the turn synchronously
    return d, system, fake_stream, sink


@patch("core.conversation.driver.publish")
def test_streaming_turn_routes_content_and_tts_then_idle(pub):
    d, system, fs, sink = _driver()
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))            # endpoint -> streaming turn

    system.llm_chat.begin_stream.assert_called_once()
    sink.start.assert_called_once()
    sink.feed_chunk.assert_called_once()             # the single tts_chunk
    sink.finish.assert_called_once()
    system.llm_chat.end_stream.assert_called_once()
    assert d.engine.state == IDLE

    # both content events were routed to the bus as VOICE_TURN_CHUNK
    chunk_pubs = [c for c in pub.call_args_list
                  if c.args and c.args[0] == Events.VOICE_TURN_CHUNK]
    assert len(chunk_pubs) == 2
    assert chunk_pubs[0].args[1]["text"] == "Hi "


def test_empty_transcript_skips_stream():
    d, system, fs, sink = _driver(transcript="")
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))
    system.llm_chat.begin_stream.assert_not_called()
    sink.start.assert_not_called()
    assert d.engine.state == IDLE


def test_barge_in_cancels_generation_and_stops_sink():
    system = MagicMock()
    sink = MagicMock()
    sink._worker = None
    d = ConversationDriver(system, transcribe_fn=lambda p: "x", sink_factory=lambda: sink,
                           endpoint_silence_ms=300, min_speech_ms=100, barge_hold_ms=90)
    d._spawn = lambda target, *a: None               # suppress the turn thread
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))             # -> RESPONDING
    assert d.engine.state == RESPONDING
    d._active_sink = sink                            # simulate a turn-in-progress sink
    d.engine.arm_barge()                             # prose flowing (turn thread suppressed)

    d.push_frame(*frame(100, True))                  # barge over the response
    system.cancel_generation.assert_called_once()
    sink.stop.assert_called_once()
    assert d.engine.state == USER_SPEAKING


def test_turn_finished_noop_after_barge():
    d, system, fs, sink = _driver()
    d._spawn = lambda target, *a: None
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))
    assert d.engine.state == RESPONDING
    d.engine.arm_barge()
    d.push_frame(*frame(100, True))                  # barge -> USER_SPEAKING
    d.engine.turn_finished()                         # stale finish from the cancelled turn
    assert d.engine.state == USER_SPEAKING


# ── D1 (2026-07-09): barge arms on AUDIO, not thinking-as-content ────────────
# chat_stream emits thinking as <think>-wrapped {"type":"content"} events with NO
# tts_chunk (thinking isn't spoken). Arming on content let a reasoning model's
# silent thinking phase be barged/cancelled by a caller's talk-pause-talk cadence.

@patch("core.conversation.driver.publish")
def test_barge_not_armed_during_thinking_content_only(pub):
    """Content with no audio (a full thinking block) must never arm barge."""
    events = [
        {"type": "content", "text": "<think>"},
        {"type": "content", "text": "let me reason about this"},
        {"type": "content", "text": "</think>"},
        {"type": "done"},
    ]
    d, system, fs, sink = _driver(events=events)
    d.engine.arm_barge = MagicMock()
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))             # -> synchronous streaming turn
    d.engine.arm_barge.assert_not_called()           # no audio ever flowed → never armed


@patch("core.conversation.driver.publish")
def test_barge_arms_once_on_tts_chunk(pub):
    """With real audio, barge arms exactly once — at the tts_chunk, not on the
    thinking/prose content events that precede it."""
    events = [
        {"type": "content", "text": "<think>"},
        {"type": "content", "text": "thinking"},
        {"type": "content", "text": "</think>"},
        {"type": "content", "text": "Hello"},        # prose, still no audio yet
        {"type": "tts_chunk", "audio_b64": "x", "stream_id": "s"},
        {"type": "done"},
    ]
    d, system, fs, sink = _driver(events=events)
    d.engine.arm_barge = MagicMock()
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))
    d.engine.arm_barge.assert_called_once()          # armed only at the tts_chunk


# ── spoken failure path (2026-07-16): a failed turn must not be dead air ─────

@patch("core.conversation.driver.publish")
def test_error_event_fires_error_cue(pub):
    """A yielded {"type":"error"} event (chat_stream's non-raise fault path)
    must fire the error cue instead of ending the turn in silence."""
    events = [{"type": "error", "text": "provider down"}]
    d, system, fs, sink = _driver(events=events)
    cues = []
    d.set_cues(cues.append)
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))
    assert "error" in cues
    system.llm_chat.end_stream.assert_called_once()  # stream still closed cleanly
    assert d.engine.state == IDLE                    # line stays up for a retry


@patch("core.conversation.driver.publish")
def test_stream_exception_fires_error_cue(pub):
    """A raise out of chat_stream (dead provider) must fire the error cue."""
    def _boom(text):
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover
    d, system, fs, sink = _driver()
    fs.chat_stream.side_effect = _boom
    cues = []
    d.set_cues(cues.append)
    d.push_frame(*frame(150, True))
    for _ in range(3):
        d.push_frame(*frame(100, False))
    assert "error" in cues
    assert d.engine.state == IDLE
