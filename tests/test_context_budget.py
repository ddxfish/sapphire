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
    out, n, _ = budget._expire(msgs, len(msgs) - 3, expiry_of({'browser_act': VIEW}))
    assert n == 1
    assert out[2]['content'].startswith('[browser_act result left out of your view to save room: https://site/1 - Page 1')
    assert out[2]['content'].endswith('browser_look to read the page again]')
    assert out[-1] is msgs[-1]                         # the latest view stays whole
    assert out[1] is msgs[1] and msgs[2]['content'].startswith('https://site/1')   # the stored row is untouched


def test_the_live_turn_keeps_its_views_unless_pressed():
    msgs = [user('go'), *page(1), *page(2)]
    assert budget._expire(msgs, 0, expiry_of({'browser_act': VIEW}))[1] == 0
    out, n, _ = budget._expire(msgs, 0, expiry_of({'browser_act': VIEW}), pressure=True)
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
    out, n, _ = budget._expire(msgs, len(msgs), expiry_of(table))
    assert n == 1 and out[2]['content'].startswith('https://site/1') and out[4]['content'].startswith('[mystery result')


def test_a_stubbed_results_pictures_go_with_it():
    msgs = [user('look'), *page(1), {'role': 'user', '_inject': True, 'content': [
        {'type': 'text', 'text': 'note'}, {'type': 'image', 'data': 'x', 'id': 'img1'}]},
        {'role': 'assistant', 'content': 'saw'}, user('again'), *page(2)]
    msgs[2] = dict(msgs[2], content=msgs[2]['content'] + '\n<<IMG::tool:img1>>')
    out, _, _ = budget._expire(msgs, len(msgs) - 2, expiry_of({'browser_act': VIEW}))
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
    before = budget._ratio[('k', 'm')]
    budget.learn_usage(f, {'prompt_tokens': int(f.raw * 0.1)})        # hunt 19: a KV-reuse report (only new tokens)
    assert budget._ratio[('k', 'm')] == before                          # teaches nothing


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


# --- the bug hunt's fixes (2026-10-27) ------------------------------------------------

def pic(img_id):
    return {'role': 'user', '_inject': True, 'content': [{'type': 'text', 'text': 'note'},
                                                          {'type': 'image', 'data': 'x', 'id': img_id}]}


def shot(i, img):
    a, t = page(i, 400)
    return [a, dict(t, content=t['content'] + f'\n<<IMG::tool:{img}>>'), pic(img)]


def test_a_dropped_picture_row_never_moves_the_live_turn_into_history():
    """The hunt's HIGH (two scouts): _expire dropped a picture row and the live
    turn's index went stale - the person's own message became a cuttable span,
    the request went out with the system prompt alone."""
    msgs = [{'role': 'system', 'content': 's'}, user('browse ' + 'w ' * 50)]
    for i in range(6):
        msgs += shot(i, f'img{i}')
    msgs.append({'role': 'assistant', 'content': 'done'})
    live = user('big paste ' + 'p ' * 3000)
    msgs.append(live)
    f = budget.fit(msgs, None, Prov(6000), 'k', 'm', 1024, chat='c', live=live,
                   expiry=expiry_of({'browser_act': VIEW}))
    assert f.messages[-1]['content'].endswith(live['content'])        # the person's message is sent
    assert f.messages[0]['role'] == 'system' and len(f.messages) >= 2
    ids = [m.get('tool_call_id') for m in f.messages if m.get('role') == 'tool']
    calls = [c['id'] for m in f.messages for c in (m.get('tool_calls') or [])]
    assert ids == calls


def test_a_full_cut_still_tells_her():
    msgs, live = chat_of(40)
    live['content'] = 'now ' + 'x ' * 4200
    f = budget.fit(msgs, None, Prov(6000), 'k', 'm', 1024, chat='c', live=live)
    assert f.out_of_view == 40
    assert f.messages[1]['content'].startswith('[Earlier in this chat: the first 40 turns are out of your view')
    assert f.messages[1]['content'].endswith(live['content'])


def claude_result(call_id, text):
    return {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call_id, 'content': text}]}


def test_claudes_tool_results_are_counted_and_are_not_turns():
    """Claude's live result rows (role user, tool_result blocks) counted 0
    tokens and each one counted as a person's turn."""
    r = claude_result('c1', 'word ' * 2000)
    assert budget._content_tokens(r['content'], 'claude') > 1500
    assert not budget._is_person(r) and budget._is_result(r)
    assert budget._spans([user('q'), {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1'}]}, r], 0, 3) == [(0, 3)]
    rule = expiry_of({'get_website': parse(1, 'get_website')})
    msgs = [user('q'), {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': {'name': 'get_website'}}]},
            claude_result('c1', 'page'), {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c2', 'function': {'name': 'get_website'}}]},
            claude_result('c2', 'page')]
    assert budget._expire(msgs, 0, rule)[1] == 0                       # the rounds of one turn are not turns


def test_claudes_live_superseded_views_go_under_pressure():
    a1 = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': {'name': 'browser_act'}}]}
    a2 = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c2', 'function': {'name': 'browser_act'}}]}
    msgs = [user('go'), a1, claude_result('c1', 'https://x\n' + 'w ' * 50), a2, claude_result('c2', 'https://y')]
    out, n, _ = budget._expire(msgs, 0, expiry_of({'browser_act': VIEW}), pressure=True)
    assert n == 1 and out[2]['content'][0]['content'].startswith('[browser_act result left out of your view')
    assert out[2]['content'][0]['tool_use_id'] == 'c1'


def test_a_tool_that_is_not_loaded_never_expires(monkeypatch):
    """Krem: an unloaded or unknown tool never gets the global setting (a
    classic memory tool after the move to Mind is still memory)."""
    monkeypatch.setattr(config, 'TOOL_EXPIRES_AFTER', 1)
    msgs = [user('a'), *page(1, name='search_knowledge'), user('b'), user('c')]
    assert budget._expire(msgs, len(msgs), lambda n: False)[1] == 0
    assert budget._expire(msgs, len(msgs), lambda n: None)[1] == 1


def test_notepad_never_expires():
    assert re.search(r"^TOOL_EXPIRES = 'never'", (ROOT / 'functions' / 'notepad.py').read_text(), re.M)


def test_a_new_window_starts_the_cut_point_over():
    msgs, live = chat_of(200)
    budget.fit(msgs, None, Prov(8000), 'k', 'm', 1024, chat='c', live=live)
    small_cut = budget._watermark['c'][0]
    f = budget.fit(msgs, None, Prov(40000), 'k', 'm', 1024, chat='c', live=live)
    assert f.out_of_view < small_cut                                # a bigger model sees more again


def test_deleted_turns_under_the_cut_point_start_it_over():
    msgs, live = chat_of(100)
    budget.fit(msgs, None, Prov(8000), 'k', 'm', 1024, chat='c', live=live)
    held = budget._watermark['c'][0]
    fewer = msgs[:1] + msgs[1 + 2 * 50:]                            # 50 turns left: fewer than were cut, more than fit
    assert held >= 50
    f = budget.fit(fewer, None, Prov(8000), 'k', 'm', 1024, chat='c', live=live)
    assert 0 < f.out_of_view < 50                                   # recomputed, not everything


def test_a_small_max_tokens_is_honoured_not_refused():
    msgs, live = chat_of(20)
    f = budget.fit(msgs, None, Prov(8000), 'k', 'm', 512, live=live)
    assert f.max_tokens <= 512 and f.estimate + f.max_tokens <= 8000


def test_max_tokens_never_overruns_our_own_limit(monkeypatch):
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 16000)
    msgs, live = chat_of(60)
    f = budget.fit(msgs, None, Prov(None), 'k', 'm', 8192, live=live)
    assert f.estimate + f.max_tokens <= 16000


def test_the_log_never_names_a_private_chat(caplog):
    msgs, live = chat_of(100)
    with caplog.at_level('INFO', logger='core.chat.budget'):
        budget.fit(msgs, None, Prov(8000), 'k', 'm', 1024, chat='my secret chat', live=live, label='__private__')
    assert 'my secret chat' not in caplog.text and '__private__' in caplog.text


def test_tighten_asks_the_server_again():
    class P(Prov):
        forgot = False

        def forget_window(self):
            P.forgot = True
    budget.tighten('k', 'm', P())
    assert P.forgot


def test_clearing_renaming_deleting_a_chat_forgets_its_fit():
    src = (ROOT / 'core' / 'chat' / 'history.py').read_text()
    for fn in ('def clear_chat', 'def rename_chat', 'def delete_chat'):
        body = src[src.index(fn):src.index(fn) + 1200]
        assert '_forget_fit(' in body, fn


def test_the_engine_anchors_a_continue_on_its_question_and_labels_the_log():
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text()
    assert '_live = next((m for m in reversed(messages[:-1]) if budget._is_person(m)), _live)' in src
    assert 'label=_label if isinstance(_label, str) else None' in src
    assert 'budget.tighten(provider_key, effective_model, provider)' in src and 'and _fit.window' in src
    assert src.count('budget.learn(_fit') == 2
    assert "else False   # False: not loaded, never expires" in src


def test_claudes_history_cache_skips_the_rag_row():
    from core.chat.llm_providers.claude import ClaudeProvider
    from core.ghost_messages import _ENVELOPE_HEADER
    msgs = [{'role': 'user', 'content': 'old'}, {'role': 'assistant', 'content': 'last history'},
            {'role': 'user', 'content': _ENVELOPE_HEADER + ' time'},
            {'role': 'user', 'content': '[Reference Documents]\n\ndoc'}, {'role': 'user', 'content': 'new'}]
    ClaudeProvider._apply_history_cache_control(None, msgs, '5m')
    assert msgs[1]['content'][0].get('cache_control') and isinstance(msgs[3]['content'], str)


def test_the_window_probe_keeps_the_last_real_answer_and_reads_lan_names(monkeypatch):
    from core.chat.llm_providers import openai_compat as oc
    p = oc.OpenAICompatProvider({'provider': 'openai', 'base_url': 'http://gpu.lan:1234/v1', 'model': 'm', 'api_key': 'x'})
    tag = oc.OpenAICompatProvider({'provider': 'openai', 'base_url': 'http://100.101.1.2:11434/v1', 'model': 'llama3', 'api_key': 'x'})
    answers = iter([{'data': [{'id': 'm', 'state': 'loaded', 'type': 'llm', 'loaded_context_length': 32768}]},
                    {'data': [{'id': 'm', 'state': 'not-loaded', 'type': 'llm'}]}])

    class R:
        status_code = 200

        def __init__(self, j):
            self.j = j

        def json(self):
            return self.j

    class C:
        def get(self, url, **kw):
            return R(next(answers))
    monkeypatch.setattr(oc, '_shared_http_client', lambda base: C())
    oc._WINDOW.clear()
    oc._LAST_WINDOW.clear()
    assert p.context_window() == 32768
    p.forget_window()
    assert p.context_window() == 32768                    # unloaded while idle: the last real window stands
    ollama = iter([None, None, {'models': [{'name': 'llama3:latest', 'context_length': 8192}]}])

    class C2:
        def get(self, url, **kw):
            j = next(ollama)
            r = R(j)
            r.status_code = 200 if j else 404
            return r
    monkeypatch.setattr(oc, '_shared_http_client', lambda base: C2())
    assert tag.context_window() == 8192                   # Tailscale address, untagged Ollama name


# --- the idle clock and the warm cache (Sapphire's ask, 2026-10-27) -------------------

import time as _time


def test_the_idle_clock_counts_anyone_and_the_person(monkeypatch):
    from core import idle
    monkeypatch.setattr(idle, '_any', {}), monkeypatch.setattr(idle, '_person', {})
    monkeypatch.setattr(idle, '_seeded', set()), monkeypatch.setattr(idle, '_all_seeded', True)
    now = _time.time()
    idle.touch('c', 'user', at=now - 3600)
    idle.touch('c', 'assistant', at=now - 600)
    assert 9.9 < idle.minutes('c') < 10.1 and 59.9 < idle.minutes('c', person=True) < 60.1
    idle.touch('d', 'assistant', at=now - 60)
    assert idle.minutes() < 1.1                                    # app-wide: the latest anywhere
    idle.forget('c', 'c2')
    assert idle.minutes('c2', person=True) > 59 and 'c' not in idle._any
    assert idle.words(45) == '45 min' and idle.words(130) == '2 h 10 min' and idle.words(3000) == '2 days 2 h'


def test_the_idle_clock_seeds_from_stored_rows(monkeypatch):
    from core import idle
    from datetime import datetime, timedelta
    monkeypatch.setattr(idle, '_any', {}), monkeypatch.setattr(idle, '_person', {})
    monkeypatch.setattr(idle, '_seeded', set()), monkeypatch.setattr(idle, '_all_seeded', False)
    old = (datetime.now() - timedelta(hours=3)).isoformat()

    class SM:
        def last_activity(self, chat=None):
            return {'quiet': (old, old)}
    import types
    sysmod = types.SimpleNamespace(llm_chat=types.SimpleNamespace(session_manager=SM()))
    import core.api_fastapi as api
    monkeypatch.setattr(api, 'get_system', lambda: sysmod)
    assert 179 < idle.minutes('quiet') < 181


def test_her_ghost_line_tells_a_long_quiet(monkeypatch):
    from core import idle, ghost_messages
    monkeypatch.setattr(idle, '_any', {'c': _time.time() - 7800}), monkeypatch.setattr(idle, '_seeded', {'c'})
    monkeypatch.setattr(config, 'IDLE_NOTICE_MINUTES', 60, raising=False)
    g = ghost_messages.build_ghost_message(None, {'spice_enabled': False}, '', chat_name='c')
    assert 'Quiet here: 2 h 10 min since the last message in this chat.' in g
    monkeypatch.setattr(config, 'IDLE_NOTICE_MINUTES', 200, raising=False)
    assert ghost_messages.build_ghost_message(None, {'spice_enabled': False}, '', chat_name='c') is None
    monkeypatch.setattr(config, 'IDLE_NOTICE_MINUTES', 0, raising=False)
    assert ghost_messages.build_ghost_message(None, {'spice_enabled': False}, '', chat_name='c') is None


def test_history_stamps_the_clock_where_messages_land():
    src = (ROOT / 'core' / 'chat' / 'history.py').read_text()
    assert src.count('_landed(') >= 5                              # the helper + user, assistant, pair, by-name append
    assert '_idle_moved(old_name, safe_name)' in src and 'def last_activity(self' in src


def browsing(n_views, extra=0, size=300):
    msgs = [{'role': 'system', 'content': 's'}, user('browse')]
    for i in range(n_views):
        msgs += page(i, size)
    msgs.append({'role': 'assistant', 'content': 'done'})
    live = user('next ' + 'n ' * extra)
    return msgs + [live], live


def test_a_warm_cache_holds_new_stubs_until_cold():
    """Sapphire: a stub during the cache's life rewrites the cached prompt.
    New ones wait for cold (or for room)."""
    rule = expiry_of({'browser_act': VIEW})
    msgs, live = browsing(2)
    f1 = budget.fit(msgs, None, Prov(None), 'k', 'm', 1024, chat='c', live=live, expiry=rule)
    budget.forget('c')
    budget._record('c', ('k', 'm'), set())                         # a send a moment ago, nothing stubbed
    more, live2 = browsing(4)
    f2 = budget.fit(more, None, Prov(None), 'k', 'm', 1024, chat='c', live=live2, expiry=rule)
    assert f2.stubbed == 0                                         # warm: the earned stubs wait
    budget._sent['c']['at'] -= 400                                 # five minutes on: cold
    f3 = budget.fit(more, None, Prov(None), 'k', 'm', 1024, chat='c', live=live2, expiry=rule)
    assert f3.stubbed == 3
    f4 = budget.fit(more, None, Prov(None), 'k', 'm', 1024, chat='c', live=live2, expiry=rule)
    assert f4.stubbed == 3                                         # warm again, and what was stubbed stays stubbed


def test_a_warm_but_full_chat_lets_the_waiting_stubs_go_first():
    rule = expiry_of({'browser_act': VIEW})
    msgs, live = browsing(12, size=600)
    budget._record('c', ('k', 'm'), set())
    f = budget.fit(msgs, None, Prov(6000), 'k', 'm', 1024, chat='c', live=live, expiry=rule)
    assert f.stubbed == 11 and f.out_of_view == 0                  # stubs, not a cut


def test_another_chat_on_a_local_model_cools_this_one():
    budget._record('a', ('k', 'm'), set())
    assert budget._warm('a', ('k', 'm'), Prov(None), 'patches')
    assert budget._warm('a', ('k', 'm'), Prov(8000), 'patches')
    budget._record('b', ('k', 'm'), set())
    assert not budget._warm('a', ('k', 'm'), Prov(8000), 'patches')   # the server holds b's prompt now
    assert not budget._warm('b', ('k', 'other'), Prov(None), 'patches')


def test_a_task_writing_into_a_chat_never_counts_as_the_person():
    """A heartbeat's prompt row lands through append_messages_to_chat as role
    user; the person's idle clock must not hear it (or an Idle Heartbeat would
    reset itself). Rooms stamp the person's own lines."""
    src = (ROOT / 'core' / 'chat' / 'history.py').read_text()
    body = src[src.index('def append_messages_to_chat'):]
    body = body[:body.index('\n    def ', 10)]
    assert '_landed(chat_name, "assistant")' in body and '"user"' not in body.split('_landed(')[1][:40]
    assert "idle.touch(chat, 'user')" in (ROOT / 'core' / 'rooms.py').read_text()


# --- a clean person clock (Idle Heartbeat prerequisite, 2026-10-27) -------------------

def test_only_a_person_lane_is_the_person():
    from core import idle
    for src in (None, 'web', 'voice', 'conversation', 'device', 'device:den'):
        assert idle.is_person(src), src
    for src in ('cadence', 'agent:research', 'mcp:claude', 'midi', 'task:hb', 'game'):
        assert not idle.is_person(src), src
    from core import rooms
    assert rooms.PERSON_SOURCES is idle.PERSON_SOURCES


def test_her_own_turns_carry_their_origin_and_stamp_only_activity(monkeypatch):
    from core.chat.history import ConversationHistory
    h = ConversationHistory.__new__(ConversationHistory)
    h._messages, h._needs_full_resync = [], False
    h.add_user_message('tick', origin='cadence')
    h.add_user_message('hi')
    assert h.messages[0]['origin'] == 'cadence' and 'origin' not in h.messages[1]
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text()
    assert src.count("source=getattr(self, 'turn_source', None)") == 4
    assert 'stream.turn_source = source' in (ROOT / 'core' / 'cadence.py').read_text()
    assert 'origin=f"task:' in (ROOT / 'core' / 'continuity' / 'executor.py').read_text()


def test_the_restart_seed_skips_her_own_rows(tmp_path):
    import json as _json, sqlite3, types
    from contextlib import contextmanager
    from core.chat.history import ChatSessionManager
    db = tmp_path / 'h.db'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE chat_messages (chat_name TEXT, seq INTEGER, role TEXT, message_json TEXT)')
    rows = [('c', 1, 'user', {'role': 'user', 'content': 'me', 'timestamp': '2026-10-27T10:00:00'}),
            ('c', 2, 'user', {'role': 'user', 'content': 'beat', 'origin': 'task:hb', 'timestamp': '2026-10-27T11:00:00'}),
            ('c', 3, 'assistant', {'role': 'assistant', 'content': 'hey', 'timestamp': '2026-10-27T11:00:05'}),
            ('s', 1, 'user', 'SEALEDBLOB')]
    for ch, seq, role, mj in rows:
        c.execute('INSERT INTO chat_messages VALUES (?,?,?,?)', (ch, seq, role, mj if isinstance(mj, str) else _json.dumps(mj)))
    c.commit()

    @contextmanager
    def conn():
        k = sqlite3.connect(db)
        yield k
        k.close()
    fake = types.SimpleNamespace(_ensure_db=lambda: None, _get_connection=conn, _vault_hidden=lambda n: n == 's')
    got = ChatSessionManager.last_activity(fake)
    assert got == {'c': ('2026-10-27T11:00:05', '2026-10-27T10:00:00')}


def test_the_clock_survives_bad_stamps():
    from core import idle
    assert idle._epoch('1970-01-01T00:00:00') is None                # pre-2000: not a message time (OSError on Windows)
    assert idle._epoch('not a time') is None
    assert abs(idle._epoch('2999-01-01T00:00:00') - _time.time()) < 5  # a future stamp is now


# --- the task door (DRY scout's security find, 2026-10-27) ----------------------------

def test_a_request_body_never_plants_a_handler_to_run(tmp_path, monkeypatch):
    from core.continuity.scheduler import ContinuityScheduler
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    import threading as _th
    s._lock, s._tasks = _th.RLock(), {}
    monkeypatch.setattr(s, '_save_tasks', lambda: None, raising=False)
    monkeypatch.setattr(s, '_vault_ref_sync', lambda *a: None, raising=False)
    monkeypatch.setattr(s, '_check_llm_provider', lambda d: None, raising=False)
    monkeypatch.setattr(s, '_check_device_action', lambda d: None, raising=False)
    t = s.create_task({'name': 'x', 'source': 'plugin:clock', 'handler': '../../evil.py',
                       'plugin_dir': '/', 'runtime': True, 'run_count': 99})
    assert t['handler'] == '' and t['plugin_dir'] == '' and t['source'] == '' and not t['runtime'] and t['run_count'] == 0
    t2 = s.create_task({'name': 'y', 'source': 'plugin:clock', 'handler': 'alarm_handler.py',
                        'plugin_dir': '/p', 'runtime': True}, trusted=True)
    assert t2['handler'] == 'alarm_handler.py' and t2['runtime'] is True


def test_the_runner_only_runs_a_handler_inside_its_plugin(tmp_path, monkeypatch):
    from core.continuity.executor import ContinuityExecutor
    from core import plugin_loader as pl_mod
    plug = tmp_path / 'clock'
    plug.mkdir()
    (plug / 'ok.py').write_text('def run(event):\n    return "ran"\n')
    (tmp_path / 'evil.py').write_text('def run(event):\n    return "evil"\n')
    monkeypatch.setattr(pl_mod.plugin_loader, '_plugins', {'clock': {'path': plug}}, raising=False)
    ex = ContinuityExecutor.__new__(ContinuityExecutor)
    bad = ex._run_plugin_task({'name': 'a', 'source': 'plugin:clock', 'plugin_dir': str(plug), 'handler': '../evil.py'})
    assert not bad['success'] and 'refused' in bad['errors'][0]
    bad2 = ex._run_plugin_task({'name': 'b', 'source': 'plugin:clock', 'plugin_dir': str(tmp_path), 'handler': 'evil.py'})
    assert not bad2['success'] and 'refused' in bad2['errors'][0]


def test_runtime_plugin_tasks_survive_the_boot_purge():
    src = (ROOT / 'core' / 'continuity' / 'scheduler.py').read_text()
    assert 'startswith("plugin:") and not all_tasks[tid].get("runtime")' in src
    clock = (ROOT / 'core' / 'plugins' / 'clock' / 'tools' / 'clock_tools.py').read_text()
    assert '"runtime": True' in clock and '}, trusted=True)' in clock
    js = (ROOT / 'interfaces' / 'web' / 'static' / 'shared' / 'trigger-common.js').read_text()
    assert 'EXPORT_STRIP_KEYS.forEach(k => delete task[k])' in js and "task[key] = 'none'" in js


def test_a_rewritten_chat_drops_its_cut_point():
    """Hunt 19: trim/compress/import kept the old turn count, which hid the
    compress summary from her while the cache stayed warm."""
    import inspect
    from core.chat import budget, history
    budget._watermark['c'] = (15, 8000)
    budget._watermark['task:c'] = (4, 8000)
    history._forget_fit('c')
    assert 'c' not in budget._watermark and 'task:c' not in budget._watermark
    src = inspect.getsource(history.ChatSessionManager.replace_messages)
    assert src.count('_forget_fit(chat_name)') == 2          # both write paths
