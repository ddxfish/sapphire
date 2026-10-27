"""The one context budgeter (core/chat/budget.py) and tool expiry.

The trigger, 2026-10-27: LM Studio held 56,320 tokens, Context Limit said 128K,
the prompt reached 56,296 and the model got 24 tokens to answer. Retrying sent
the same prompt into the same wall."""
import base64
import io
import re
from pathlib import Path

import pytest

import config
from core.chat import budget
from core.chat.function_manager import FunctionManager

ROOT = Path(__file__).resolve().parent.parent
parse = FunctionManager.parse_expires
VIEW = parse({'after': 'superseded', 'group': 'browser-view', 'hint': 'browser_look to read the page again'}, 'x')


class Prov:
    provider_name = 'openai'
    base_url = 'http://127.0.0.1:1234/v1'

    def __init__(self, window=None):
        self.window = window

    def context_window(self):
        return self.window


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    budget.forget()
    budget._ratio.clear()
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 0, raising=False)
    monkeypatch.setattr(config, 'TOOL_EXPIRES_AFTER', 0, raising=False)
    yield
    budget.forget()
    budget._ratio.clear()


def user(t):
    return {'role': 'user', 'content': t}


def page(i, n=3000, name='browser_act'):
    return [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': f'c{i}', 'type': 'function',
             'function': {'name': name, 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': f'c{i}', 'name': name,
             'content': f'https://site/{i} - Page {i}\n' + 'word ' * n}]


def turn(i, words=200):
    return [user(f'question {i} ' + 'q ' * words), {'role': 'assistant', 'content': f'answer {i} ' + 'a ' * words}]


def expiry_of(table):
    return lambda name: table.get(name)


# --- the key ------------------------------------------------------------------------

@pytest.mark.parametrize('raw, want', [
    ('never', {'after': 'never', 'group': 't', 'hint': ''}),
    (3, {'after': 3, 'group': 't', 'hint': ''}),
    ('3', {'after': 3, 'group': 't', 'hint': ''}),
    ('Superseded', {'after': 'superseded', 'group': 't', 'hint': ''}),
    ({'after': 'superseded', 'group': 'g', 'hint': 'look again'}, {'after': 'superseded', 'group': 'g', 'hint': 'look again'}),
])
def test_an_expires_value_is_read_with_care(raw, want):
    assert parse(raw, 't') == want


@pytest.mark.parametrize('raw', [None, 0, -2, True, 'soon', {'after': 'later'}, [1]])
def test_a_malformed_or_missing_expires_is_undeclared(raw):
    assert parse(raw, 't') is None


def test_the_tools_that_matter_declare_it():
    """Memory never leaves her view (Krem: it's who she is); page views replace
    each other; web reads go after 3 turns."""
    import json
    for p in ('memory', 'mindpalace', 'remembrance'):
        assert json.loads((ROOT / 'core' / 'plugins' / p / 'plugin.json').read_text())['tool_expires'] == 'never'
    src = (ROOT / 'core' / 'plugins' / 'browser' / 'tools' / 'browser.py').read_text()
    assert src.count('"group": "browser-view"') == 3
    assert re.search(r'^TOOL_EXPIRES = 3\b', (ROOT / 'functions' / 'web.py').read_text(), re.M)


def test_the_key_never_reaches_a_provider():
    from core.chat.llm_providers.base import BaseProvider
    tool = {'type': 'function', 'expires': {'after': 3}, 'function': {'name': 'x'}}
    assert BaseProvider.convert_tools_for_api(None, [tool]) == [{'type': 'function', 'function': {'name': 'x'}}]


# --- expiry -------------------------------------------------------------------------

def test_an_old_page_view_is_one_line_once_a_later_one_exists():
    msgs = [user('look'), *page(1), {'role': 'assistant', 'content': 'saw it'}, user('next'), *page(2)]
    out, n = budget._expire(msgs, len(msgs) - 3, expiry_of({'browser_act': VIEW}))
    assert n == 1
    assert out[2]['content'].startswith('[browser_act result left out of your view to save room: https://site/1 - Page 1')
    assert out[2]['content'].endswith('browser_look to read the page again]')
    assert out[-1] is msgs[-1]                         # the latest view stays whole
    assert out[1] is msgs[1] and msgs[2]['content'].startswith('https://site/1')   # the stored row is untouched


def test_the_live_turn_keeps_its_views_unless_pressed():
    msgs = [user('go'), *page(1), *page(2)]
    assert budget._expire(msgs, 0, expiry_of({'browser_act': VIEW}))[1] == 0
    out, n = budget._expire(msgs, 0, expiry_of({'browser_act': VIEW}), pressure=True)
    assert n == 1 and out[2]['content'].startswith('[browser_act result left out')
    assert budget._expire(out, 0, expiry_of({'browser_act': VIEW}), pressure=True)[1] == 0   # a stub is never stubbed again


def test_after_n_counts_the_persons_turns_only():
    rule = expiry_of({'get_website': parse(3, 'get_website')})
    msgs = [user('read it'), *page(1, name='get_website'), {'role': 'assistant', 'content': 'ok'}]
    for i in range(2):
        msgs += [{'role': 'user', 'content': 'ghost', '_inject': True}, user(f'u{i}'), {'role': 'assistant', 'content': 'a'}]
    assert budget._expire(msgs, len(msgs), rule)[1] == 0            # 2 turns later (ghost rows aren't turns)
    msgs += [user('third'), {'role': 'assistant', 'content': 'a'}]
    assert budget._expire(msgs, len(msgs), rule)[1] == 1


def test_never_and_undeclared(monkeypatch):
    msgs = [user('a'), *page(1, name='read_self'), *page(2, name='mystery')] + [user('b'), user('c'), user('d')]
    table = {'read_self': parse('never', 'read_self')}
    assert budget._expire(msgs, len(msgs), expiry_of(table))[1] == 0
    monkeypatch.setattr(config, 'TOOL_EXPIRES_AFTER', 2)
    out, n = budget._expire(msgs, len(msgs), expiry_of(table))
    assert n == 1 and out[2]['content'].startswith('https://site/1') and out[4]['content'].startswith('[mystery result')


def test_a_stubbed_results_pictures_go_with_it():
    msgs = [user('look'), *page(1), {'role': 'user', '_inject': True, 'content': [
        {'type': 'text', 'text': 'note'}, {'type': 'image', 'data': 'x', 'id': 'img1'}]},
        {'role': 'assistant', 'content': 'saw'}, user('again'), *page(2)]
    msgs[2] = dict(msgs[2], content=msgs[2]['content'] + '\n<<IMG::tool:img1>>')
    out, _ = budget._expire(msgs, len(msgs) - 2, expiry_of({'browser_act': VIEW}))
    assert not any(m.get('_inject') for m in out)


# --- the fit ------------------------------------------------------------------------

def chat_of(n_turns, live_text='now'):
    msgs = [{'role': 'system', 'content': 'You are Sapphire.'}]
    for i in range(n_turns):
        msgs += turn(i)
    live = user(live_text)
    return msgs + [live], live


def test_the_smaller_window_wins_and_the_reply_keeps_its_room(monkeypatch):
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 128000)
    msgs, live = chat_of(60)
    f = budget.fit(msgs, None, Prov(8000), 'lmstudio', 'qwen', 8192, chat='c', live=live)
    assert f.window == 8000 and f.server == 8000 and f.limit == 128000
    assert f.estimate + f.max_tokens <= 8000
    assert f.max_tokens >= budget.REPLY_FLOOR


def test_the_trigger_case_never_starves_the_reply():
    """56,320 held, a chat far past it: the oldest turns go, the reply has room."""
    msgs, live = chat_of(400)
    f = budget.fit(msgs, None, Prov(56320), 'lmstudio', 'qwen', 4096, chat='c', live=live)
    assert f.out_of_view > 0 and f.estimate + 4096 <= 56320
    assert f.max_tokens == 4096


def test_a_cut_takes_whole_turns_and_tells_her():
    msgs = [{'role': 'system', 'content': 's'}]
    for i in range(40):
        msgs += [user(f'q{i} ' + 'q ' * 300), *page(i, 300, name='read_self'), {'role': 'assistant', 'content': 'a ' * 300}]
    live = user('now')
    msgs.append(live)
    f = budget.fit(msgs, None, Prov(8000), 'k', 'm', 1024, chat='c', live=live)
    first = f.messages[1]
    assert first['role'] == 'user' and first['content'].startswith(f'[Earlier in this chat: the first {f.out_of_view} turns are out of your view')
    ids = [m.get('tool_call_id') for m in f.messages if m.get('role') == 'tool']
    calls = [c['id'] for m in f.messages for c in (m.get('tool_calls') or [])]
    assert ids == calls                                 # no result without its call, no call without its result
    assert f.messages[-1] is live


def test_the_cut_point_holds_still_as_the_chat_grows():
    """Cut to ~65%, then held until full again: the front (the cached prefix)
    moves rarely, not every turn."""
    msgs, live = chat_of(100)
    seen = []
    for i in range(12):
        f = budget.fit(msgs, None, Prov(16000), 'k', 'm', 1024, chat='c', live=live)
        seen.append(f.out_of_view)
        msgs = msgs[:-1] + turn(1000 + i) + [user(f'next {i}')]
        live = msgs[-1]
    assert len(set(seen)) <= 3 and seen == sorted(seen)


def test_it_all_fits_again_and_the_watermark_lets_go():
    msgs, live = chat_of(100)
    budget.fit(msgs, None, Prov(16000), 'k', 'm', 1024, chat='c', live=live)
    assert budget._watermark.get('c')
    f = budget.fit(msgs, None, Prov(None), 'k', 'm', 1024, chat='c', live=live)     # no limit at all now
    assert f.out_of_view == 0 and f.messages == msgs
    small, live2 = chat_of(2)
    budget.fit(small, None, Prov(16000), 'k', 'm', 1024, chat='c', live=live2)
    assert 'c' not in budget._watermark


def test_a_turn_too_big_alone_is_honest_words_not_a_send():
    live = user('x ' * 20000)
    with pytest.raises(budget.ContextFull, match='This turn alone is about'):
        budget.fit([{'role': 'system', 'content': 's'}, live], None, Prov(8000), 'k', 'm', 1024, live=live)


def test_tools_too_big_say_so():
    tools = [{'type': 'function', 'function': {'name': f't{i}', 'description': 'd ' * 400}} for i in range(30)]
    live = user('hi')
    with pytest.raises(budget.ContextFull, match='Her tools alone'):
        budget.fit([live], tools, Prov(8000), 'k', 'm', 1024, live=live)


def test_the_live_turns_own_old_views_go_before_giving_up():
    live = user('browse')
    msgs = [{'role': 'system', 'content': 's'}, live]
    for i in range(8):
        msgs += page(i, 1500)
    f = budget.fit(msgs, None, Prov(9000), 'k', 'm', 1024, live=live, expiry=expiry_of({'browser_act': VIEW}))
    assert f.stubbed >= 1 and f.messages[-1] is msgs[-1]


def test_a_cloud_window_leaves_max_tokens_alone(monkeypatch):
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 20000)
    msgs, live = chat_of(5)
    f = budget.fit(msgs, None, Prov(None), 'k', 'm', 16000, live=live)
    assert f.window == 20000 and f.max_tokens == 16000


def test_no_window_sends_all():
    msgs, live = chat_of(50)
    f = budget.fit(msgs, None, Prov(None), 'k', 'm', 1024, live=live)
    assert f.messages == msgs and f.window is None


def test_the_ratio_learns_from_the_providers_count():
    msgs, live = chat_of(3)
    f = budget.fit(msgs, None, Prov(None), 'k', 'm', 1024, live=live)
    budget.learn(f, {'prompt': f.raw * 2, 'estimated': False})
    assert budget._ratio[('k', 'm')] == 2.0
    budget.learn(f, {'prompt': 999999, 'estimated': True})          # an estimate teaches nothing
    assert budget._ratio[('k', 'm')] == 2.0
    budget.learn_usage(f, {'prompt_tokens': f.raw, 'cache_read_tokens': 0})
    assert budget._ratio[('k', 'm')] == pytest.approx(0.7 * 2.0 + 0.3 * 1.0)
    budget.tighten('k', 'm')
    assert budget._ratio[('k', 'm')] == pytest.approx((0.7 * 2.0 + 0.3) * 1.25)


def test_pictures_cost_what_the_model_charges():
    assert budget.image_tokens(1280, 4000, 'patches') == 46 * 143 + 2     # a full-page shot on a local Qwen-VL
    assert 900 < budget.image_tokens(1280, 4000, 'claude') < 1600
    assert budget.image_tokens(1024, 1024, 'openai') == 765 and budget.image_tokens(512, 512, 'openai') == 255
    assert budget.image_tokens(700, 700, 'gemini') == 258
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (280, 56)).save(buf, 'PNG')
    block = {'type': 'image', 'data': base64.b64encode(buf.getvalue()).decode()}
    assert budget._content_tokens([block], 'patches') == 10 * 2 + 2


# --- wiring -------------------------------------------------------------------------

def test_every_provider_call_sends_the_fit():
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text()
    calls = re.findall(r'chat_completion_stream\(\s*(\w[\w.]*)', src)
    assert calls == ['_fit.messages', '_fit.messages']
    assert src.count('_fit, _gp = _fitted(') == 2 and 'budget.learn(_fit' in src
    assert 'is_context_error(e) and chunk_count == 0 and not _ctx_retried' in src
    ex = (ROOT / 'core' / 'continuity' / 'execution_context.py').read_text()
    assert 'self.provider, fitted.messages, gen_params, tools=self.tools' in ex
    assert 'context_limit * 0.9' not in ex                          # its old trim is gone
    chat = (ROOT / 'core' / 'chat' / 'chat.py').read_text()
    assert chat.count('get_messages_for_llm(context_limit=0)') == 2  # no second trim before the fit


def test_the_metrics_label_never_renames_the_turns_chat():
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text()
    assert 'chat_name = self.main_chat.session_manager.metrics_chat_label()' not in src


def test_the_app_marks_its_own_rows():
    chat = (ROOT / 'core' / 'chat' / 'chat.py').read_text()
    assert '"content": ghost_text, "_inject": True' in chat and '"content": rag_context, "_inject": True' in chat
    assert '"_inject": True' in (ROOT / 'core' / 'chat' / 'history.py').read_text()


def test_its_refusal_is_the_words():
    from core.chat.llm_errors import explain, is_context_error
    w = explain(budget.ContextFull('This turn alone is about 9,000 tokens and the model holds 8,000 tokens'))
    assert w['text'].startswith('This turn alone') and w['go']['key'] == 'CONTEXT_LIMIT'
    assert is_context_error(Exception("the request exceeds the available context size (n_ctx)"))
    assert not is_context_error(budget.ContextFull('context window'))


# --- the meter ----------------------------------------------------------------------

def test_the_meter_spans_our_limit_with_a_notch_at_the_models_window(monkeypatch):
    """Krem's vote: Context Limit is the bar's 100%; the server's smaller window
    is a notch; the fill is what was really sent."""
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 128000)
    msgs, live = chat_of(30)
    f = budget.fit(msgs, None, Prov(56320), 'k', 'm', 4096, chat='c', live=live)
    m = budget.meter('c', 999999)
    assert m['measured'] and m['used'] == f.estimate and m['limit'] == 128000
    assert m['notch'] == 44.0 and m['server'] == 56320
    assert m['percent'] == int(f.estimate * 100 / 128000)


def test_before_any_turn_the_meter_counts_the_chat():
    m = budget.meter('fresh', 5000)
    assert not m['measured'] and m['used'] == 5000 and m['limit'] == 0 and m['notch'] is None


def test_both_routes_read_the_one_meter():
    src = (ROOT / 'core' / 'routes' / 'chat.py').read_text()
    assert src.count('budget.meter(') == 2
    assert 'count_tokens(m.get("thinking", "")' not in src          # thinking is never sent; it never counted
    js = (ROOT / 'interfaces' / 'web' / 'static' / 'api.js').read_text()
    assert "getElementById('context-notch')" in js
    assert '<div id="context-notch"></div>' in (ROOT / 'interfaces' / 'web' / 'static' / 'surface' / 'chat-mode.js').read_text()
