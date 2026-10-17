# Two-ledger regen (plan tmp/regen-two-ledger-plan.md, F1-A/F2 rulings
# 2026-08-24): Regenerate rewinds the chat but the journal kept the moves —
# world ahead of context, Qwen's "we're already in the hall" spiral fuel.
# rewind_to_match drops journal turns anchored at/past the current message
# count (the chat no longer holds their messages), salvaging the player's
# sealed text. Conservative: unanchored doomed events = no-op.
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import state as st  # noqa: E402

SLUG = "goblin-den"
CHAT = "rewind-chat"


def _turn(turn, idx, *extra):
    """One journal turn: tick + extra events, all anchored at msg_index."""
    evs = [{"event": "turn_tick", "turn": turn, "msg_index": idx}]
    for e in extra:
        evs.append({**e, "turn": turn, "msg_index": idx})
    return evs


def test_rewind_drops_doomed_turns_and_replays():
    for e in (_turn(1, 2, {"event": "moved", "to": 2})
              + _turn(2, 4, {"event": "moved", "to": 3})):
        st.append(SLUG, CHAT, e)
    # Regen of turn 2: chat truncated back to 4 messages → count == anchor
    rw = st.rewind_to_match(SLUG, CHAT, 4)
    assert rw == {"turn": 1, "dropped": 2, "salvaged": 0}
    state = st.replay(SLUG, CHAT)
    assert state["turn"] == 1 and state["room"] == 2   # turn 2's move undone
    arch = st._cs().read_all(CHAT, f"story:reverted:{SLUG}")
    assert [e["turn"] for e in arch] == [2, 2]         # forensics, same law


def test_deeper_regen_rewinds_multiple_turns():
    for t, idx in ((1, 2), (2, 4), (3, 6)):
        for e in _turn(t, idx):
            st.append(SLUG, CHAT, e)
    rw = st.rewind_to_match(SLUG, CHAT, 4)             # regen 2 turns back
    assert rw["turn"] == 1 and rw["dropped"] == 2
    assert st.replay(SLUG, CHAT)["turn"] == 1


def test_no_divergence_is_a_noop():
    for e in _turn(1, 2):
        st.append(SLUG, CHAT, e)
    assert st.rewind_to_match(SLUG, CHAT, 4) is None   # normal next turn
    assert len(st.read_journal(SLUG, CHAT)) == 1


def test_unanchored_doomed_event_bails_whole():
    # F1 conservative rule: surgery only on full anchor coverage.
    for e in _turn(1, 2):
        st.append(SLUG, CHAT, e)
    st.append(SLUG, CHAT, {"event": "turn_tick", "turn": 2, "msg_index": 4})
    st.append(SLUG, CHAT, {"event": "moved", "room": 3, "turn": 2})  # no anchor
    assert st.rewind_to_match(SLUG, CHAT, 4) is None
    assert len(st.read_journal(SLUG, CHAT)) == 3       # untouched


def test_sealed_text_survives_the_rewind():
    # Krem's chest (F2): fill + reveal died in the doomed span — the reveal
    # drops (chest re-closes), the player's words are salvaged at the cut,
    # so the ✉ chip offers them back, editable, before she finds it again.
    for e in (_turn(1, 2)
              + _turn(2, 4,
                      {"event": "sealed", "key": "1:chest:open", "text": "seven rings"},
                      {"event": "revealed", "key": "1:chest:open"})):
        st.append(SLUG, CHAT, e)
    rw = st.rewind_to_match(SLUG, CHAT, 4)
    assert rw["salvaged"] == 1
    state = st.replay(SLUG, CHAT)
    assert state["seals"]["1:chest:open"] == "seven rings"
    assert "1:chest:open" not in state["revealed"]
    # the salvaged event re-anchors fresh (or not at all) — never doomed-looking
    salv = [e for e in st.read_journal(SLUG, CHAT) if e["event"] == "sealed"][0]
    assert salv["turn"] == 1 and salv.get("msg_index", -1) < 4


def test_all_turns_doomed_keeps_setup_fills():
    # Turn-0 setup (sealed slot values) predates any tick — a full rewind
    # keeps it: the playthrough returns to its authored start, words intact.
    st.append(SLUG, CHAT, {"event": "sealed", "key": "1:chest:open",
                           "text": "the deed", "turn": 0, "msg_index": 0})
    for e in _turn(1, 2, {"event": "moved", "to": 2}):
        st.append(SLUG, CHAT, e)
    rw = st.rewind_to_match(SLUG, CHAT, 2)
    assert rw["turn"] == 0
    state = st.replay(SLUG, CHAT)
    assert state["turn"] == 0 and state["seals"]["1:chest:open"] == "the deed"
