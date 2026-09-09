# F6 rail port (2026-09-09): table talk is the chat. The sealed seat plays
# MOVES only; the chat rail sees the table through the engine's RAIL SET
# (view_public + build_ghost) on the ghost envelope; the seat hears the
# table through the talk MIRROR (pre_chat / post_chat -> state['talk']).
# Also the Dark Horse 500 class: views without my_name/opp_name.
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import gameroom_core as gc  # noqa: E402

POKER = PLUGIN_DIR.parent / "game-holdem" / "games" / "poker" / "engine.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _hook(name):
    return _load(PLUGIN_DIR / "hooks" / f"{name}.py", f"gameroom_test_hook_{name}")


# A Dark-Horse-shaped engine: summary views WITHOUT names, no hidden info.
class NamelessEngine:
    CONTRACT = "seat {ai_name} vs {opp_name}"
    BANTER_CONTRACT = "wall {ai_name} vs {opp_name}"

    @staticmethod
    def view_for_ai(state):
        return {"wave": state.get("wave", 0)}

    view_between = view_for_ai

    @staticmethod
    def build_banter_msg(view):
        return f"wave {view['wave']}"

    @staticmethod
    def safe_action(state):
        return "noop", {}


class RailEngine(NamelessEngine):
    @staticmethod
    def view_public(state):
        return {"wave": state.get("wave", 0), "public": True}

    @staticmethod
    def build_ghost(view):
        return f"wave {view['wave']} ({view['my_name']} vs {view['opp_name']})"


def _state(**kw):
    st = {"session": {"player_name": "Krem", "ai_name": "Sapphire"},
          "talk": [], "talk_seq": 0}
    st.update(kw)
    return st


# ── names fallback (the Dark Horse table-talk 500) ──────────────────────────

def test_names_fill_from_session():
    v = gc._names({"wave": 3}, _state())
    assert v == {"wave": 3, "my_name": "Sapphire", "opp_name": "Krem"}


def test_names_never_overwrite_engine_names():
    v = gc._names({"my_name": "Rose", "opp_name": "Jack"}, _state())
    assert v["my_name"] == "Rose" and v["opp_name"] == "Jack"


def test_banter_reply_survives_nameless_view(monkeypatch):
    seen = {}

    def fake_llm(system, user, cfg):
        seen["system"] = system
        return '{"say": "hold the bend"}'
    monkeypatch.setattr(gc, "_call_llm", fake_llm)
    monkeypatch.setattr(gc, "_resolve_persona_prompt", lambda n: None)
    monkeypatch.setattr(gc, "_resolve_prompt_text", lambda n: None)
    say = gc.banter_reply(NamelessEngine, _state(wave=4), {"persona": None})
    assert say == "hold the bend"
    assert "Sapphire" in seen["system"] and "Krem" in seen["system"]


def test_decide_survives_nameless_view(monkeypatch):
    monkeypatch.setattr(gc, "_call_llm", lambda s, u, c: None)   # provider down -> fallback
    eng = types.SimpleNamespace(
        view_for_ai=lambda st: {"wave": 1}, safe_action=lambda st: ("noop", {}),
        CONTRACT="{ai_name} {opp_name}", build_user_msg=lambda v: "go",
        validate_decision=lambda d, v: None)
    monkeypatch.setattr(gc, "_resolve_persona_prompt", lambda n: None)
    monkeypatch.setattr(gc, "_resolve_prompt_text", lambda n: None)
    d = gc.decide(eng, _state(), {})
    assert d["fallback"] and d["action"] == "noop"


# ── public view / ghost block ───────────────────────────────────────────────

def test_public_view_prefers_view_public():
    v = gc.public_view(RailEngine, _state(wave=2))
    assert v["public"] is True and v["my_name"] == "Sapphire"


def test_public_view_without_rail_set_is_between_only():
    # no round live -> view_between is safe
    assert gc.public_view(NamelessEngine, _state(wave=1)) == {
        "wave": 1, "my_name": "Sapphire", "opp_name": "Krem"}
    # a live hand -> nothing (view_for_ai may carry hidden info)
    live = _state(wave=1, hand={"street": "flop"})
    assert gc.public_view(NamelessEngine, live) is None


def test_round_live():
    assert not gc.round_live(_state())
    assert gc.round_live(_state(hand={"street": "turn"}))
    assert not gc.round_live(_state(hand={"street": "over"}))
    assert gc.round_live(_state(round={"phase": "defense"}))
    assert not gc.round_live(_state(round={"phase": "over"}))


def test_ghost_block_renders_public_view(monkeypatch, hermetic_chat_store):
    monkeypatch.setattr(gc, "get_game", lambda gid: ({"title": "Towers"}, RailEngine))
    gc.save_state("towers", _state(wave=7), session="sess-a")
    block = gc.ghost_block("towers", "sess-a")
    assert block == "Game Room, Towers — live table: wave 7 (Sapphire vs Krem)"


def test_ghost_block_none_without_state_or_renderer(monkeypatch, hermetic_chat_store):
    monkeypatch.setattr(gc, "get_game", lambda gid: ({"title": "Towers"}, RailEngine))
    assert gc.ghost_block("towers", "never-dealt") is None
    monkeypatch.setattr(gc, "get_game", lambda gid: ({"title": "X"}, NamelessEngine))
    gc.save_state("x", _state(), session="sess-b")
    assert gc.ghost_block("x", "sess-b") is None


# ── the talk mirror ─────────────────────────────────────────────────────────

def test_record_talk_appends_and_caps(hermetic_chat_store):
    gc.save_state("g", _state(), session="s1")
    assert gc.record_talk("g", "s1", "player", "  needle  ")
    assert gc.record_talk("g", "s1", "ai", "x" * 900)
    talk = gc.load_state("g", session="s1")["talk"]
    assert [t["who"] for t in talk] == ["player", "ai"]
    assert talk[0]["text"] == "needle" and len(talk[1]["text"]) == 400
    assert talk[1]["seq"] == 2


def test_record_talk_noop_without_state_or_text(hermetic_chat_store):
    assert not gc.record_talk("g", "nope", "player", "hi")
    gc.save_state("g", _state(), session="s2")
    assert not gc.record_talk("g", "s2", "player", "   ")
    assert gc.load_state("g", session="s2")["talk"] == []


def _system_with(sm):
    return types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=sm))


def test_game_session_resolves_only_room_games(monkeypatch, hermetic_chat_store):
    sm = hermetic_chat_store
    sm.create_chat("game-chat")
    sm.set_named_chat_settings("game-chat", {"mode": "game", "game_id": "poker"})
    sm.create_chat("story-chat")
    sm.set_named_chat_settings("story-chat", {"mode": "game", "game_id": "story:titanic"})
    sm.create_chat("plain")
    import core.api_fastapi as af
    monkeypatch.setattr(af, "get_system", lambda: _system_with(sm))
    assert gc.game_session("game-chat") == "poker"
    assert gc.game_session("story-chat") is None
    assert gc.game_session("plain") is None
    assert gc.game_session("missing") is None
    assert gc.game_session(None) is None


def test_mirror_hooks_land_player_then_ai(monkeypatch, hermetic_chat_store):
    mirror = _hook("mirror")
    monkeypatch.setattr(gc, "game_session", lambda chat: "g" if chat == "tbl" else None)
    gc.save_state("g", _state(), session="tbl")
    ev = types.SimpleNamespace(chat_name="tbl", input="bluffing?", response="always.")
    mirror.pre_chat(ev)
    mirror.post_chat(ev)
    talk = gc.load_state("g", session="tbl")["talk"]
    assert [(t["who"], t["text"]) for t in talk] == [("player", "bluffing?"), ("ai", "always.")]
    assert all(t["via"] == "chat" for t in talk)      # the board's strip skips these


def test_mirror_ignores_other_chats_and_missing_identity(monkeypatch, hermetic_chat_store):
    mirror = _hook("mirror")
    monkeypatch.setattr(gc, "game_session", lambda chat: "g" if chat == "tbl" else None)
    gc.save_state("g", _state(), session="tbl")
    mirror.pre_chat(types.SimpleNamespace(chat_name="elsewhere", input="hi", response=None))
    mirror.pre_chat(types.SimpleNamespace(chat_name=None, input="hi", response=None))
    assert gc.load_state("g", session="tbl")["talk"] == []


def test_mirror_never_raises(monkeypatch, hermetic_chat_store):
    mirror = _hook("mirror")

    def boom(chat):
        raise RuntimeError("sealed")
    monkeypatch.setattr(gc, "game_session", boom)
    ev = types.SimpleNamespace(chat_name="tbl", input="hi", response="yo")
    assert mirror.pre_chat(ev) is ev and mirror.post_chat(ev) is ev


# ── the ghost hook's game branch ────────────────────────────────────────────

def test_ghost_hook_contributes_game_block(monkeypatch, hermetic_chat_store):
    ghost = _hook("ghost")
    from gameroom_story import state as st
    monkeypatch.setattr(st, "get_active_entry", lambda chat: None)
    monkeypatch.setattr(gc, "game_session", lambda chat: "towers")
    monkeypatch.setattr(gc, "get_game", lambda gid: ({"title": "Towers"}, RailEngine))
    gc.save_state("towers", _state(wave=9), session="tbl")
    ev = types.SimpleNamespace(chat_name="tbl", metadata={}, ghost_text=None, input="hey")
    ghost.handle(ev)
    assert ev.ghost_text.startswith("Game Room, Towers — live table: wave 9")


def test_ghost_hook_silent_on_plain_chat(monkeypatch, hermetic_chat_store):
    ghost = _hook("ghost")
    from gameroom_story import state as st
    monkeypatch.setattr(st, "get_active_entry", lambda chat: None)
    monkeypatch.setattr(gc, "game_session", lambda chat: None)
    ev = types.SimpleNamespace(chat_name="plain", metadata={}, ghost_text=None, input="hey")
    ghost.handle(ev)
    assert ev.ghost_text is None


def test_ghost_hook_story_still_wins(monkeypatch, hermetic_chat_store):
    # a paused story returns before any game lookup (the story branch owns
    # the chat); the game branch never runs for story-tagged chats
    ghost = _hook("ghost")
    from gameroom_story import state as st
    monkeypatch.setattr(st, "get_active_entry", lambda chat: {"story": "x", "paused": True})
    called = []
    monkeypatch.setattr(gc, "game_session", lambda chat: called.append(chat))
    ev = types.SimpleNamespace(chat_name="tale", metadata={}, ghost_text=None, input="hey")
    ghost.handle(ev)
    assert ev.ghost_text is None and called == []


# ── act: moves carry no talk line ───────────────────────────────────────────

def test_act_without_say_adds_no_talk_line(monkeypatch, hermetic_chat_store):
    from routes import play
    applied = []
    eng = types.SimpleNamespace(
        whose_turn=lambda st: "player",
        apply_action=lambda st, who, a, args: applied.append((who, a, args)),
        redact=lambda st: {"talk": st["talk"]})
    monkeypatch.setattr(gc, "get_game", lambda gid: ({}, eng))
    monkeypatch.setattr(gc, "run_ai_turns", lambda *a, **k: None)
    monkeypatch.setattr(gc, "session_cfg", lambda s: {})
    monkeypatch.setattr(gc, "game_settings", lambda g: {})
    gc.save_state("g", _state(), session="tbl")
    out = play.act("g", body={"action": "check", "session": "tbl"})
    assert applied == [("player", "check", {})]
    assert out["talk"] == []                       # no '(plays in silence)' marker
    # words on a move land as the player's line; this engine has no banter
    # set, so no answer is forced (plan A guard) — nothing crashes
    play.act("g", body={"action": "call", "say": "sure", "session": "tbl"})
    assert [(t["who"], t["text"]) for t in gc.load_state("g", session="tbl")["talk"]] == [("player", "sure")]


# ── the shipped poker engine's rail set ─────────────────────────────────────

@pytest.mark.skipif(not POKER.exists(), reason="game-holdem not present")
def test_poker_public_view_hides_hole_cards():
    poker = _load(POKER, "gameroom_test_poker_engine")
    state = poker.new_session(player_name="Krem", ai_name="Sapphire", cfg={})
    poker.start_round(state)
    v = poker.view_public(state)
    assert "my_hole" not in v and "hole" not in v and "deck" not in v
    assert v["my_name"] == "Sapphire" and v["opp_name"] == "Krem"
    assert v["street"] == "preflop" and v["hand_num"] == 1
    line = poker.build_ghost(gc._names(v, state))
    assert "hand #1" in line and "hole" in line.lower()
    for card in state["hand"]["hole"]["ai"] + state["hand"]["hole"]["player"]:
        assert card not in line


@pytest.mark.skipif(not POKER.exists(), reason="game-holdem not present")
def test_poker_ghost_between_hands():
    poker = _load(POKER, "gameroom_test_poker_engine")
    state = poker.new_session(player_name="Krem", ai_name="Sapphire", cfg={})
    line = poker.build_ghost(poker.view_public(state))
    assert line.startswith("between hands")


# ── one table, one transcript (plan A, 2026-09-09) ──────────────────────────

class SeatEngine(RailEngine):
    @staticmethod
    def describe_action(action, args=None):
        return {'raise': f"raise to {(args or {}).get('amount')}"}.get(action)


def test_describe_action_engine_then_fallbacks():
    assert gc.describe_action(SeatEngine, 'raise', {'amount': 20}) == 'raise to 20'
    assert gc.describe_action(SeatEngine, 'call') == 'call'                 # engine returned None
    assert gc.describe_action(NamelessEngine, 'bet', {'amount': 40}) == 'bet 40'
    assert gc.describe_action(NamelessEngine, 'new_session') == 'sit down'
    assert gc.describe_action(NamelessEngine, 'start') == 'deal'


def test_table_pair_move_with_words_and_quip():
    st = _state()
    before = st['talk_seq']
    gc.add_talk(st, 'player', 'you blinked')
    gc.add_talk(st, 'dealer', 'Flop: Ks 7d 2c')
    gc.add_talk(st, 'ai', 'Did I? Call.')
    gc.add_talk(st, 'ai', 'earlier chat reply', via='chat')   # mirrored — never in the pair
    rows = gc.table_pair(SeatEngine, st, before, 'raise', {'amount': 20}, say='you blinked')
    assert rows[0]['role'] == 'user' and rows[0]['content'] == '↳ raise to 20 — you blinked'
    assert rows[1]['role'] == 'assistant'
    assert rows[1]['content'] == '\U0001f0a0 Flop: Ks 7d 2c\nDid I? Call.'
    assert rows[0]['metadata'] == {'source': 'table'} == rows[1]['metadata']


def test_table_pair_silent_move_nothing_fresh_is_none():
    st = _state()
    assert gc.table_pair(SeatEngine, st, st['talk_seq'], 'checkpoint') is None


def test_table_pair_words_without_banter_set_just_log_them():
    st = _state()
    before = st['talk_seq']
    gc.add_talk(st, 'player', 'gg')
    eng = types.SimpleNamespace()                  # no banter set at all
    rows = gc.table_pair(eng, st, before, 'fold', say='gg')
    assert rows[0]['content'] == '↳ fold — gg' and rows[1]['content'] == '…'
    assert [t['who'] for t in st['talk']] == ['player']


def test_table_pair_words_without_quip_force_her_answer(monkeypatch):
    monkeypatch.setattr(gc, 'banter_reply', lambda *a, **k: 'Nice fold.')
    st = _state()
    before = st['talk_seq']
    gc.add_talk(st, 'player', 'gg')
    gc.add_talk(st, 'dealer', 'Sapphire wins 40 (fold)')
    rows = gc.table_pair(SeatEngine, st, before, 'fold', say='gg')
    assert rows[0]['content'] == '↳ fold — gg'
    assert rows[1]['content'] == '\U0001f0a0 Sapphire wins 40 (fold)\nNice fold.'
    assert st['talk'][-1] == {'who': 'ai', 'text': 'Nice fold.', 'hand': 0, 'seq': 3}


def test_table_pair_dealer_only_still_lands():
    st = _state()
    before = st['talk_seq']
    gc.add_talk(st, 'dealer', 'New session. Shuffle up.')
    rows = gc.table_pair(SeatEngine, st, before, 'new_session')
    assert rows[0]['content'] == '↳ sit down'
    assert rows[1]['content'] == '\U0001f0a0 New session. Shuffle up.'


def test_append_table_goes_by_name_and_reports(monkeypatch):
    calls = []

    class SM:
        def append_messages_to_chat(self, chat, rows, max_wait_if_streaming=60.0):
            calls.append((chat, rows, max_wait_if_streaming))
            return chat != 'sealed'
    import core.api_fastapi as af
    monkeypatch.setattr(af, 'get_system', lambda: _system_with(SM()))
    rows = [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}]
    assert gc.append_table('tbl', rows) is True
    assert calls == [('tbl', rows, 20.0)]
    assert gc.append_table('sealed', rows) is False
    assert gc.append_table(None, rows) is False and gc.append_table('tbl', None) is False


def test_act_logs_the_pair_after_the_lock(monkeypatch, hermetic_chat_store):
    from routes import play
    landed = []
    eng = types.SimpleNamespace(
        whose_turn=lambda st: 'player',
        apply_action=lambda st, who, a, args: gc.add_talk(st, 'dealer', 'Turn: 9h'),
        redact=lambda st: {'talk': st['talk']},
        describe_action=lambda a, args=None: 'call' if a == 'call' else None)
    monkeypatch.setattr(gc, 'get_game', lambda gid: ({}, eng))
    monkeypatch.setattr(gc, 'run_ai_turns', lambda e, st, c, g: gc.add_talk(st, 'ai', 'Ship it.'))
    monkeypatch.setattr(gc, 'session_cfg', lambda s: {})
    monkeypatch.setattr(gc, 'game_settings', lambda g: {})
    monkeypatch.setattr(gc, 'append_table',
                        lambda session, rows: (landed.append((session, rows)) or True) if rows else False)
    gc.save_state('g', _state(), session='tbl')
    out = play.act('g', body={'action': 'call', 'say': 'nice try', 'session': 'tbl'})
    assert len(landed) == 1
    session, rows = landed[0]
    assert session == 'tbl'
    assert rows[0]['content'] == '↳ call — nice try'
    assert rows[1]['content'] == '\U0001f0a0 Turn: 9h\nShip it.'
    # the subtitle's source: the landed pair rides the response, verbatim
    assert out['last'] == {'user': rows[0]['content'], 'assistant': rows[1]['content'], 'logged': True}
    # a silent verb logs nothing and says nothing
    out2 = play.act('g', body={'action': '_checkpoint', 'session': 'tbl'})
    assert len(landed) == 1 and out2['last'] is None


@pytest.mark.skipif(not POKER.exists(), reason="game-holdem not present")
def test_poker_describe_action():
    poker = _load(POKER, "gameroom_test_poker_engine")
    assert poker.describe_action('raise', {'amount': 60}) == 'raise to 60'
    assert poker.describe_action('start') == 'deal'
    assert poker.describe_action('weird') == 'weird'
