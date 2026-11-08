"""The "Disable thinking" box on an OpenAI-compatible provider works for ANY
model name (2026-11-08). It used to send a switch only to aliases containing
"qwen" (plus GLM / DeepSeek / Fireworks), so a launcher alias like
"ternary-bonsai-27b" got nothing. Proven on llama-server b12198: either
`reasoning_effort: "none"` or `chat_template_kwargs.enable_thinking: false`
stops Qwen3.5 thinking, and unknown fields are ignored. A strict endpoint that
refuses one learns it once (OPTIONAL_PARAMS) and is retried without it."""
from unittest.mock import MagicMock, patch

import pytest

from core.chat.llm_providers import openai_compat as oc


def _prov(model='ternary-bonsai-27b', base='http://127.0.0.1:8642/v1', **cfg):
    with patch.object(oc.OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = oc.OpenAICompatProvider()
    p.model, p.base_url = model, base
    p.config = {'disable_thinking': True, **cfg}
    p.OWNS_THINKING_SWITCH = False
    p._is_fireworks_reasoning_model = lambda: 'fireworks' in base
    p.fill = lambda x: x
    return p


def _inject(p, want=False):
    kw = {}
    p._inject_thinking_control(kw, want_disable=want)
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


@pytest.mark.parametrize('param', ['reasoning_effort', 'chat_template_kwargs'])
def test_a_strict_endpoint_that_refuses_a_switch_learns_it_once(param):
    p = _prov()
    p._rejected_params = set()
    kw = _inject(p)
    err = Exception(f"Unrecognized request argument supplied: {param}")
    assert p._params_named(err, kw) == [param]
    p._rejected_params.add(param)
    p._strip_rejected(kw)
    assert param not in kw and param not in (kw.get('extra_body') or {})
