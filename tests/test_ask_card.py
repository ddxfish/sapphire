"""The question card (ask_user): core/ask_card.py, functions/ask_user.py and the
one UI-marker list (core/ui_markers.py) both strip sites now share."""
import json
import re
from pathlib import Path

import pytest

from core import ask_card, ui_markers
from core.chat.chat_tool_calling import strip_ui_markers
from functions import ask_user

ROOT = Path(__file__).resolve().parent.parent

Q = [{'question': 'Which is your fav color?', 'header': 'Color',
      'options': [{'label': 'Blue', 'description': 'calm'}, {'label': 'Red'}, 'Green']},
     {'question': 'Pet?', 'options': ['Dog', 'Cat'], 'multi_select': True}]


class TestNormalize:
    def test_shape_strings_become_options_and_flags_survive(self):
        p = ask_card.normalize(Q)
        qs = p['questions']
        assert [q['question'] for q in qs] == ['Which is your fav color?', 'Pet?']
        assert qs[0]['header'] == 'Color' and 'header' not in qs[1]
        assert [o['label'] for o in qs[0]['options']] == ['Blue', 'Red', 'Green']
        assert qs[0]['options'][0]['description'] == 'calm' and 'description' not in qs[0]['options'][1]
        assert qs[1]['multi_select'] is True and 'multi_select' not in qs[0]

    def test_a_single_object_is_one_question(self):
        assert len(ask_card.normalize(Q[0])['questions']) == 1

    @pytest.mark.parametrize('bad,msg', [
        (None, 'list of 1-4'), ([], 'list of 1-4'), ('x', 'list of 1-4'),
        ([{'question': 'q', 'options': ['a']}], 'needs 2-6 distinct'),
        ([{'question': 'q', 'options': ['a', 'a']}], 'needs 2-6 distinct'),
        ([{'question': '', 'options': ['a', 'b']}], 'has no text'),
        ([{'question': 'q', 'options': ['a', 'b']}] * 5, 'at most 4'),
        (['q'], 'must be an object'),
    ])
    def test_refusals_say_what_to_fix(self, bad, msg):
        with pytest.raises(ValueError, match=re.escape(msg)):
            ask_card.normalize(bad)

    def test_stringified_lists_are_parsed_not_iterated_by_character(self):
        """Local models stringify nested arrays; the card drew `[`, `{`, `"`...
        as options and said success (chaos scout, 2026-10-07)."""
        q = ask_card.normalize([{'question': 'Color?', 'options': '["Red", {"label": "Blue"}]'}])['questions'][0]
        assert [o['label'] for o in q['options']] == ['Red', 'Blue']
        q = ask_card.normalize([{'question': 'Color?', 'options': 'Red, Blue, Green'}])['questions'][0]
        assert [o['label'] for o in q['options']] == ['Red', 'Blue', 'Green']
        qs = ask_card.normalize('[{"question": "Pet?", "options": ["Dog", "Cat"]}]')['questions']
        assert qs[0]['question'] == 'Pet?'
        with pytest.raises(ValueError, match='needs 2-6'):
            ask_card.normalize([{'question': 'Color?', 'options': 'just-one-word'}])

    def test_limits_one_line_and_the_comment_cannot_be_closed_early(self):
        p = ask_card.normalize([{'question': 'a\nb --> c', 'options': ['x' * 500, 'y']}])
        q = p['questions'][0]
        assert '\n' not in q['question'] and '-->' not in q['question']
        assert len(q['options'][0]['label']) == 80
        assert len(ask_card.normalize([{'question': 'q', 'options': list('abcdefgh')}])['questions'][0]['options']) == 6


class TestMarker:
    def test_marker_round_trips_and_strips(self):
        m = ask_card.marker(Q)
        assert m.startswith('<!--ASK:{') and m.endswith('}-->') and '\n' not in m
        assert json.loads(m[len('<!--ASK:'):-3])['questions'][1]['multi_select'] is True
        text = f"Card shown.\n{m}"
        assert ask_card.strip(text) == 'Card shown.'
        assert ask_card.strip('plain') == 'plain' and ask_card.strip('') == ''

    def test_the_model_never_sees_it_live_or_on_replay(self):
        text = f"Card shown.\n{ask_card.marker(Q)}"
        assert strip_ui_markers(text) == 'Card shown.'
        assert ui_markers.UI_MARKER_RE.sub('', text).strip() == 'Card shown.'
        # a history-less lane keeps the browser's markers in its persisted copy
        assert '<!--ASK:' in strip_ui_markers(text, keep_img=True)
        assert ui_markers.has_marker(text) and not ui_markers.has_marker('plain') and not ui_markers.has_marker(None)

    def test_one_list_feeds_both_strip_sites(self):
        src = (ROOT / 'core' / 'chat' / 'history.py').read_text(encoding='utf-8')
        assert 'from core.ui_markers import UI_MARKER_RE' in src
        assert 'GALLERY:[' not in src, 'history.py grew its own marker regex again'
        src = (ROOT / 'core' / 'chat' / 'chat_tool_calling.py').read_text(encoding='utf-8')
        assert 'UI_MARKER_KEEP_IMG_RE' in src and 'FILES:' not in src
        # the siblings still strip, with IMG kept where it must be
        for m in ('<<IMG::abc>>', '<!--GALLERY:[{"u":1}]-->', '<!--FILES:{"items":[]}-->'):
            assert ui_markers.UI_MARKER_RE.sub('', f"t {m}").strip() == 't'
        assert ui_markers.UI_MARKER_KEEP_IMG_RE.sub('', 'a <<IMG::x>> <<FILE::y>>').strip() == 'a <<IMG::x>>'


class TestTool:
    def test_execute_returns_the_card_and_tells_her_to_end_the_turn(self):
        text, ok = ask_user.execute('ask_user', {'questions': Q}, None)
        assert ok
        head, marker = text.split('\n', 1)
        assert '2 questions' in head and 'end your turn' in head
        assert marker == ask_card.marker(Q)

    def test_bad_input_is_a_failed_call_with_the_reason(self):
        text, ok = ask_user.execute('ask_user', {'questions': [{'question': 'q', 'options': ['a']}]}, None)
        assert not ok and 'distinct options' in text
        text, ok = ask_user.execute('ask_user', {}, None)
        assert not ok

    def test_tool_schema_mirrors_askuserquestion_and_is_local(self):
        tool = ask_user.TOOLS[0]
        assert tool['is_local'] is True and tool['function']['name'] == 'ask_user'
        item = tool['function']['parameters']['properties']['questions']['items']
        assert set(item['properties']) == {'question', 'header', 'options', 'multi_select'}
        assert item['required'] == ['question', 'options']
        assert 'ask_user' in ask_user.AVAILABLE_FUNCTIONS

    def test_default_toolsets_carry_it_except_the_agent_baseline(self):
        """'default' is the lean background-worker baseline the llm agent kind runs
        on: an agent that 'asks' a card nobody sees ends its mission on a question
        (chaos scout, 2026-10-07). Every chat-facing default set has it."""
        d = json.loads((ROOT / 'core' / 'toolsets' / 'toolsets.json').read_text(encoding='utf-8'))
        sets = {k for k, v in d.items() if isinstance(v, dict) and 'functions' in v}
        assert 'ask_user' not in d['default']['functions']
        assert all('ask_user' in d[k]['functions'] for k in sets - {'default'})


class TestBrowserWiring:
    """The three renderers parse the marker through the one module, and the
    answer rides the normal send path."""

    def test_renderers_import_the_one_module(self):
        static = ROOT / 'interfaces' / 'web' / 'static'
        for f in ('ui-streaming.js', 'ui-parsing.js'):
            src = (static / f).read_text(encoding='utf-8')
            assert "from './shared/ask-marker.js'" in src and 'parseAskMarker(' in src and 'buildAskCards(' in src
        ui = (static / 'ui.js').read_text(encoding='utf-8')
        assert ui.count('lockAnsweredAskCards(chat)') == 3        # history render + user message + finish swap
        ev = (static / 'core' / 'events.js').read_text(encoding='utf-8')
        assert "'sapphire:ask_answer'" in ev and 'triggerSendWithText(text)' in ev
        card = (static / 'shared' / 'ask-marker.js').read_text(encoding='utf-8')
        assert "new CustomEvent('sapphire:ask_answer'" in card

    def test_the_answer_leads_with_the_inbox_header(self):
        """Every door that isn't the user typing leads with inbox.header(...);
        the card's answer (Krem, 2026-10-07: 'so she knows it's not from me')
        uses the very same grammar, spelled once in the JS."""
        from core.chat import inbox
        card = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'ask-marker.js').read_text(encoding='utf-8')
        m = re.search(r"export const ANSWER_HEADER = '(.+?)';", card)
        assert m
        js = m.group(1).encode('utf-8').decode('unicode_escape').encode('latin-1').decode('utf-8')
        assert js == inbox.header('Question card', 'ask_user', 'what the user clicked')

    def test_dictation_no_longer_drops_while_a_turn_is_live(self):
        src = (ROOT / 'interfaces' / 'web' / 'static' / 'handlers' / 'send-handlers.js').read_text(encoding='utf-8')
        body = src.split('export async function triggerSendWithText')[1].split('\n}\n')[0]
        assert 'getIsProc()' not in body, 'triggerSendWithText still early-outs on a live turn'
