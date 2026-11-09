"""Continue that works, or fails with words (2026-11-08).

A trailing assistant row is resumed by llama.cpp on its own; vLLM and SGLang
close it and answer fresh unless sent continue_final_message +
add_generation_prompt false (CONTINUE_FLAGS). A server that ignores them is
caught by the probe (continues_reply) and Continue is refused there. Ollama's
'does not support thinking' names no field, so the thinking switches are
dropped on any 400 about thinking and the request retried."""
from unittest.mock import MagicMock, patch

import pytest

from core.chat.llm_providers import openai_compat as oc


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(oc, '_CONTINUES', {})
    monkeypatch.setattr(oc, '_notify', lambda *a, **k: None)


def _prov(model='my-brain', base='http://127.0.0.1:8650/v1', **cfg):
    with patch.object(oc.OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = oc.OpenAICompatProvider()
    p.model, p.base_url = model, base
    p.config = {'display_name': 'Test', **cfg}
    p.OWNS_THINKING_SWITCH = False
    p._is_fireworks_reasoning_model = lambda: False
    p.fill = lambda x: x
    p._rejected_params, p._refused_switches = set(), set()
    p._hdr_kwargs = lambda: {}
    return p


def _wired(p, *side):
    p._client = MagicMock()
    p._client.chat.completions.create = MagicMock(side_effect=list(side))
    return p._client.chat.completions.create


def _reply(text):
    r = MagicMock()
    r.choices[0].message.content = text
    return r


class _Refused(Exception):
    status_code = 400


TAIL = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'I am'}]


def test_a_trailing_assistant_row_gets_both_continue_flags():
    kw = {'messages': TAIL}
    _prov()._inject_continue(kw)
    assert kw['extra_body'] == oc.CONTINUE_FLAGS
    assert set(kw[oc._SWITCHES_KEY]) == set(oc.CONTINUE_FLAGS)


@pytest.mark.parametrize('last', [{'role': 'user', 'content': 'hi'},
                                  {'role': 'assistant', 'content': '', 'tool_calls': [{}]},
                                  {'role': 'tool', 'content': 'r'}])
def test_any_other_tail_gets_nothing(last):
    kw = {'messages': [last]}
    _prov()._inject_continue(kw)
    assert kw == {'messages': [last]}


def test_the_users_own_flag_wins_and_a_refused_one_stays_away():
    p = _prov()
    p._refused_switches = {'add_generation_prompt'}
    kw = {'messages': TAIL, 'extra_body': {'continue_final_message': False}}
    p._inject_continue(kw)
    assert kw['extra_body'] == {'continue_final_message': False}
    assert oc._SWITCHES_KEY not in kw


def test_a_strict_server_that_names_a_flag_unlearns_it():
    p = _prov()
    create = _wired(p, _Refused("Unrecognized request argument supplied: continue_final_message"), 'ok')
    kw = {'model': 'm', 'messages': TAIL}
    p._inject_continue(kw)
    assert p._create(kw) == 'ok'
    assert create.call_args_list[1].kwargs['extra_body'] == {'add_generation_prompt': False}
    assert p._refused_switches == {'continue_final_message'}


def test_ollama_does_not_support_thinking_drops_the_thinking_switches_only():
    p = _prov()
    create = _wired(p, _Refused('model "llama3.1" does not support thinking'), 'ok')
    kw = {'model': 'm', 'messages': TAIL}
    p._inject_thinking_control(kw, want_disable=True)
    p._inject_continue(kw)
    assert p._create(kw) == 'ok'
    sent = create.call_args_list[1].kwargs
    assert 'reasoning_effort' not in sent
    assert sent['extra_body'] == oc.CONTINUE_FLAGS
    assert p._refused_switches == set(oc.THINKING_SWITCHES)


def test_a_thinking_error_that_is_not_a_400_is_left_to_fail():
    class _Down(Exception):
        status_code = 500
    p = _prov()
    _wired(p, _Down('reasoning engine crashed'))
    kw = {'model': 'm', 'messages': TAIL}
    p._inject_thinking_control(kw, want_disable=True)
    with pytest.raises(_Down):
        p._create(kw)
    assert not p._refused_switches


@pytest.mark.parametrize('said,verdict', [
    (' five, six, seven', True),                                      # streamed-style tail
    ('Counting: one, two, three, four, five, six', True),              # llama.cpp echo
    ('<think>\n\n</think>\n\nfive, six', True),
    ('one, two, three, four, five', False),                            # answered fresh
    ('Sure! One, two, three', False),
    ('', None), ('<think>let me count', None),                         # no evidence
])
def test_the_probe_reads_the_reply(said, verdict):
    p = _prov()
    create = _wired(p, _reply(said))
    assert p.continues_reply() is verdict
    sent = create.call_args.kwargs
    assert sent['messages'][-1] == {'role': 'assistant', 'content': oc._PROBE_SAID}
    assert sent['extra_body']['continue_final_message'] is True
    assert sent['reasoning_effort'] == 'none'


def test_the_probe_asks_once_per_server_and_model():
    p = _prov()
    create = _wired(p, _reply('five'))
    assert p.continues_reply() is True and p.continues_reply() is True
    assert create.call_count == 1
    other = _prov(model='other')
    _wired(other, _reply('one, two'))
    assert other.continues_reply() is False


def test_a_probe_that_errors_is_no_evidence_and_is_asked_again():
    p = _prov()
    create = _wired(p, ConnectionError('down'), _reply('five'))
    assert p.continues_reply() is None
    assert p.continues_reply() is True
    assert create.call_count == 2
