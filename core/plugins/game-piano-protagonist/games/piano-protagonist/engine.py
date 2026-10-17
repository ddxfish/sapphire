# games/piano-protagonist/engine.py — Piano Protagonist, server half.
# The piano runs CLIENT-side (app/piano-protagonist.js, a free-mount board):
# the notes fall, the keys sound and the optional score is made in the
# browser, and none of it is kept. This side is the least the Game Room asks
# of a game: a session to sit in, and the "Your songs" setting.
#
# She has no part in it yet: no seat, no ghost line, no turns (plugin.json's
# room_defaults switch her cadence off). The board calls no verbs.

SETTINGS = [
    {"key": "user_songs", "label": "Your songs", "type": "text", "rows": 8, "tab": "Songs",
     "default": "",
     "help": "One per line: name | bpm | notation. Notes are C4 E4 G4:2 (C4 is middle C, :beats is length, "
             "R is a rest); a chord name with no octave (C, Am, G7) is the whole chord: C:4 G:4 Am:4 F:4. "
             "A third field before the notation, 'medium' or 'hard', sets the shelf it sits on."},
]


# ── Engine contract ───────────────────────────────────────────────────────────

def new_session(player_name="Player", ai_name="Sapphire", cfg=None):
    return {
        "session": {"player_name": player_name, "ai_name": ai_name},
        "talk": [], "talk_seq": 0,
        "round": {"phase": "idle"},
    }


def can_start(state):
    return None


def start_round(state):
    state["round"] = {"phase": "idle"}


def whose_turn(state):
    return "player"


def apply_action(state, who, action, args):
    # The host strips the silent-verb '_' prefix before this is called.
    if action == "noop":
        return
    raise IllegalActionShim(f"unknown action '{action}'")


class IllegalActionShim(Exception):
    """Swapped for gameroom_core.IllegalAction once the host is importable
    (the Dark Horse pattern): the host catches by its own class."""


def _wire():
    try:
        import gameroom_core
        global IllegalActionShim
        IllegalActionShim = gameroom_core.IllegalAction
    except Exception:
        pass


_wire()


def redact(state):
    """Nothing is hidden: the whole session is the player's."""
    return {k: v for k, v in state.items()}
