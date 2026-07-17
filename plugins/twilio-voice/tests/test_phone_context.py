"""Ghost phone-context hook (2026-07-16 rails rework).

The daemon resolves the public-line safety text per call and stamps it on the
call record as `rails`; the hook appends exactly what it's given. The stakes:
an unchecked trusted line must get NO conduct rails (she's fully herself with
Krem), while public lines keep the bait defense + stranger posture — and the
hangup instructions survive every configuration.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_PATH = Path(__file__).resolve().parent.parent / "hooks" / "phone_context.py"


def _load():
    spec = importlib.util.spec_from_file_location("twilio_phone_ctx_undertest", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pc = _load()


def _event(call):
    system = MagicMock()
    system._twilio_active_calls = {"chat1": call}
    system.llm_chat.session_manager._effective_chat_name.return_value = "chat1"
    ev = MagicMock()
    ev.metadata = {"system": system}
    ev.ghost_text = None
    return ev


def test_public_line_gets_rails():
    ev = _event({"caller": "+15551234567", "direction": "inbound", "rails": "RAILS-TEXT"})
    pc.ghost_inject(ev)
    assert "RAILS-TEXT" in ev.ghost_text
    assert "HANG UP" in ev.ghost_text


def test_trusted_line_gets_no_rails():
    ev = _event({"caller": "+15551234567", "direction": "inbound", "rails": ""})
    pc.ghost_inject(ev)
    assert "never repeat" not in ev.ghost_text       # no built-in fallback leak
    assert "stranger" not in ev.ghost_text
    assert "HANG UP" in ev.ghost_text                # hangup ability survives trust


def test_custom_note_replaces_base_and_substitutes_caller():
    ev = _event({"caller": "+15551234567", "direction": "inbound",
                 "note": "This is Krem calling from {caller}.", "rails": ""})
    pc.ghost_inject(ev)
    assert "This is Krem calling from +15551234567." in ev.ghost_text
    assert "live phone call with" not in ev.ghost_text


def test_note_plus_rails_both_present():
    ev = _event({"caller": "+15551234567", "direction": "inbound",
                 "note": "Family friend Bob.", "rails": "RAILS-TEXT"})
    pc.ghost_inject(ev)
    assert "Family friend Bob." in ev.ghost_text
    assert "RAILS-TEXT" in ev.ghost_text


def test_outbound_includes_goal_and_rails():
    ev = _event({"caller": "+15550001111", "direction": "outbound",
                 "goal": "refill the prescription", "rails": "RAILS-TEXT"})
    pc.ghost_inject(ev)
    assert "YOU placed" in ev.ghost_text
    assert "refill the prescription" in ev.ghost_text
    assert "RAILS-TEXT" in ev.ghost_text
