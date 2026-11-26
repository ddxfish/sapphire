"""A three-hour film through the real budgeter (core/chat/budget.py) at a 55K
window: the shape Movie Night gives a chat. Every turn is her unprompted one:
a cue row with the dialogue since her last line (up to the perception cap),
two frames on the live row only, her short line back. The chat died forty
minutes into a film once (no trim before the send, 2026-07); this is the
guard that a film of any length only ever loses its oldest scenes."""
import base64
import io

import pytest

from core.chat import budget

WINDOW = 55000
TURNS = 45                      # three hours at a four-minute gap
CUE_CHARS = 3800                # the board's cap on what one deposit carries


def _frame():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (910, 512), (20, 30, 40)).save(buf, 'JPEG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _tools(tokens):
    if not tokens:
        return []
    words = 'look up the thing and say what it holds '
    tool = lambda n: {'type': 'function', 'function': {'name': f'tool_{n}', 'description': words * 60,
                                                       'parameters': {'type': 'object', 'properties': {}}}}
    out = []
    while budget.tools_tokens(out) < tokens:
        out.append(tool(len(out)))
    return out


def _cue(n):
    lines = [f"[{n // 15}:{(n * 4) % 60:02d}:00]"] + [f"Line {n}.{i}: nobody move until I say so, all right?" for i in range(80)]
    return ('[Movie Night — your turn while the screen is in front of you]\n'
            + '\n'.join(lines))[:CUE_CHARS]


def _film(tool_tokens, chat):
    """Run the film; -> [(turns out of view, reply room sent, estimate)] per turn."""
    budget.forget(chat)
    tools, frame = _tools(tool_tokens), _frame()
    system = {'role': 'system', 'content': 'You are on the couch watching a film. ' * 300}
    history, seen = [], []
    for n in range(TURNS):
        cue = _cue(n)
        live = {'role': 'user', 'content': [{'type': 'text', 'text': cue},
                                            {'type': 'image', 'data': frame, 'media_type': 'image/jpeg'},
                                            {'type': 'image', 'data': frame, 'media_type': 'image/jpeg'}]}
        ghost = {'role': 'user', 'content': '[operator note: Tuesday evening]', '_inject': True}
        f = budget.fit([system, *history, ghost, live], tools, None, 'lmstudio', 'qwen', 8192,
                       chat=chat, live=live, limit=WINDOW)
        seen.append((f.out_of_view, f.max_tokens, f.estimate))
        history += [{'role': 'user', 'content': cue},                      # the stored row: the words only
                    {'role': 'assistant', 'content': f"Oh, that's not going to end well for him. ({n})"}]
    budget.forget(chat)
    return seen


@pytest.mark.parametrize('tool_tokens', [0, 10000, 20000, 30000])
def test_a_three_hour_film_is_never_refused_and_never_thrashes(tool_tokens):
    seen = _film(tool_tokens, f'movie-sim-{tool_tokens}')       # ContextFull would raise out of here
    assert all(room == 8192 for _, room, _ in seen)              # her reply room is never squeezed
    assert all(est < WINDOW for _, _, est in seen)
    cuts = sum(1 for a, b in zip(seen, seen[1:]) if b[0] != a[0])
    assert cuts <= TURNS // 3                                    # the cut is held between cuts, not remade every turn
    assert all(b[0] >= a[0] for a, b in zip(seen, seen[1:]))     # and only ever moves forward
    in_view = [n - out for n, (out, _, _) in enumerate(seen)]
    assert min(in_view[10:]) >= 3                                # she always has the last few scenes


def test_tools_are_what_decides_how_much_of_the_film_she_keeps():
    light = _film(0, 'movie-sim-light')
    heavy = _film(30000, 'movie-sim-heavy')
    assert heavy[-1][0] > light[-1][0]                           # more tools, more of the film out of view by the credits


def test_tools_past_the_window_still_refuse_in_words_not_a_dead_chat():
    with pytest.raises(budget.ContextFull) as e:
        _film(60000, 'movie-sim-too-heavy')
    assert 'tools' in str(e.value).lower()
