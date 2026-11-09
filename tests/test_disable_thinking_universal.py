"""The "Disable thinking" box on an OpenAI-compatible provider works for ANY
model name (2026-11-08). It used to send a switch only to aliases containing
"qwen" (plus GLM / DeepSeek / Fireworks), so a launcher alias like
"ternary-bonsai-27b" got nothing. Proven on llama-server b12198: either
`reasoning_effort: "none"` or `chat_template_kwargs.enable_thinking: false`
stops Qwen3.5 thinking, and unknown fields are ignored. A strict endpoint that
refuses one stops getting THAT switch (_REFUSED_SWITCHES) — never the real
values of the same names (ultrareview 2026-11-08)."""
from unittest.mock import MagicMock, patch

import pytest

from core.chat.llm_providers import openai_compat as oc


def _prov(model='ternary-bonsai-27b', base='http://127.0.0.1:8642/v1', **cfg):
    with patch.object(oc.OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = oc.OpenAICompatProvider()
    p.model, p.base_url = model, base
    p.config = {'disable_thinking': True, 'display_name': 'Test', **cfg}
    p.OWNS_THINKING_SWITCH = False
    p._is_fireworks_reasoning_model = lambda: 'fireworks' in base
    p.fill = lambda x: x
    p._refused_switches = set()
    p._rejected_params = set()
    return p


def _inject(p, want=False, kw=None):
    kw = dict(kw or {})
    p._inject_thinking_control(kw, want_disable=want)
    p.last_switches = kw.pop(oc._SWITCHES_KEY, [])
    return kw


@pytest.mark.parametrize('model', ['ternary-bonsai-27b', 'qwen3.5-4b', 'gemma-4-12b', 'Llama-3.3-70B', ''])
def test_any_alias_gets_both_open_server_switches(model):
    kw = _inject(_prov(model=model))
    assert kw['reasoning_effort'] == 'none'
    assert kw['extra_body']['chat_template_kwargs'] == {'enable_thinking': False, 'thinking': False}


def test_cloud_families_keep_their_own_switch_only():
    ds = _inject(_prov(model='deepseek-chat', base='https://api.deepseek.com/v1'))
    assert ds == {'extra_body': {'thinking': {'type': 'disabled'}}}
    glm = _inject(_prov(model='glm-5.3', base='https://api.z.ai/v1'))
    assert glm == {'extra_body': {'thinking': {'type': 'disabled'}}}
    fw = _inject(_prov(model='accounts/fireworks/models/kimi-k3', base='https://api.fireworks.ai/inference/v1'))
    assert fw == {'reasoning_effort': 'none'}


def test_the_off_path_sends_nothing():
    assert _inject(_prov(disable_thinking=False)) == {}


def test_continue_forces_it_on_any_alias():
    kw = _inject(_prov(disable_thinking=False), want=True)
    assert kw['reasoning_effort'] == 'none'


def test_the_users_raw_extra_body_still_wins():
    kw = _inject(_prov(extra_body={'chat_template_kwargs': {'enable_thinking': True}}))
    assert kw['extra_body']['chat_template_kwargs'] == {'enable_thinking': True}


class _Refused(Exception):
    status_code = 400


def _wired(p, *side):
    p._client = MagicMock()
    p._client.chat.completions.create = MagicMock(side_effect=list(side))
    p._hdr_kwargs = lambda: {}
    return p._client.chat.completions.create


def test_injected_switches_are_tagged_and_users_own_are_not():
    p = _prov()
    _inject(p)
    assert sorted(p.last_switches) == ['chat_template_kwargs', 'reasoning_effort']
    p2 = _prov(extra_body={'chat_template_kwargs': {'enable_thinking': True}})
    _inject(p2)
    assert p2.last_switches == ['reasoning_effort']


def test_strict_endpoint_refusing_one_switch_per_error_still_answers(monkeypatch):
    monkeypatch.setattr(oc, '_notify', lambda *a, **k: None)
    p = _prov(model='gpt-4o', base='https://api.openai.com/v1')
    create = _wired(p, _Refused("Unsupported parameter: 'reasoning_effort'"),
                    _Refused("Unrecognized request argument supplied: chat_template_kwargs"), 'ok')
    kw = {'model': 'gpt-4o', 'messages': []}
    p._inject_thinking_control(kw, want_disable=True)
    assert p._create(kw) == 'ok'
    last = create.call_args_list[2].kwargs
    assert 'reasoning_effort' not in last and 'extra_body' not in last
    assert oc._SWITCHES_KEY not in last
    assert p._refused_switches == {'reasoning_effort', 'chat_template_kwargs'}
    assert not p._rejected_params                     # never the learn-once strip list
    # next continue: nothing to refuse, one call
    kw2 = {'model': 'gpt-4o', 'messages': []}
    p._inject_thinking_control(kw2, want_disable=True)
    assert oc._SWITCHES_KEY not in kw2 and 'reasoning_effort' not in kw2


def test_refused_none_never_strips_fireworks_medium(monkeypatch):
    monkeypatch.setattr(oc, '_notify', lambda *a, **k: None)
    base = 'https://api.fireworks.ai/inference/v1'
    p = _prov(model='accounts/fireworks/models/kimi-k3', base=base, disable_thinking=False)
    _wired(p, _Refused("reasoning_effort 'none' is not supported"), 'ok', 'ok2')
    kw = {'model': 'm', 'messages': [], 'reasoning_effort': 'medium'}
    p._inject_thinking_control(kw, want_disable=True)     # a Continue
    assert p._create(kw) == 'ok'
    normal = {'model': 'm', 'messages': [], 'reasoning_effort': 'medium'}
    p._inject_thinking_control(normal)                    # an ordinary turn
    assert p._create(normal) == 'ok2'
    assert p._client.chat.completions.create.call_args_list[2].kwargs['reasoning_effort'] == 'medium'


def test_users_own_extra_body_value_is_never_unlearned(monkeypatch):
    monkeypatch.setattr(oc, '_notify', lambda *a, **k: None)
    p = _prov(extra_body={'chat_template_kwargs': {'enable_thinking': True}})
    create = _wired(p, _Refused("bad field chat_template_kwargs"))
    kw = {'model': 'm', 'messages': []}
    p._inject_thinking_control(kw)
    with pytest.raises(_Refused):
        p._create(kw)                       # their value — refused loudly, not stripped
    assert create.call_count == 1 and not p._refused_switches
