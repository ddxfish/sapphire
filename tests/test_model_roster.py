"""Model roster tripwires — 2026-09-20 pre-main sweep (record: tmp/model-roster-20260920.md).

What broke before this file existed: shipped dropdowns and presets carried ids
the vendors had already shut down (gemini-2.0-*, deepseek-chat/reasoner, four
Grok ids, gemma2-9b-it, ten Fireworks ids that are dedicated-GPU-only), and
two upgrades changed request semantics (Opus 5 thinks when the thinking param
is OMITTED; GPT-6 missed the gpt-5* prefix lanes). These tests pin the roster
files to a known-dead list and the request contracts to the vendor docs.

Vendor pages are not fetched here — the DEAD list is a human-verified fact
table with the verification date. Re-verify and extend it on the next roster
pass; do not delete rows.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Exact ids (not substrings) verified gone or unreachable on 2026-09-20.
DEAD_IDS = {
    # Google shut these down 2026-06-01
    'gemini-2.0-flash', 'gemini-2.0-flash-lite',
    # DeepSeek discontinued the legacy names 2026-07-24
    'deepseek-chat', 'deepseek-reasoner',
    # xAI retired 2026-05-15 (redirects today, not offered)
    'grok-4-1-fast-reasoning', 'grok-4-1-fast-non-reasoning', 'grok-3', 'grok-3-mini',
    # Groq shut down 2025-10-08
    'gemma2-9b-it',
    # Together: gone from the serverless list
    'deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct-Turbo',
    # Fireworks: model pages say "Serverless: Not supported" → 404 on a pay-per-token key
    'accounts/fireworks/models/deepseek-v3p2', 'accounts/fireworks/models/qwen3p6-plus',
    'accounts/fireworks/models/glm-5', 'accounts/fireworks/models/glm-4p7',
    'accounts/fireworks/models/minimax-m2p5', 'accounts/fireworks/models/kimi-k2-thinking',
    'accounts/fireworks/models/gemma-4-31b-it',
    'accounts/fireworks/models/qwen3-235b-a22b-thinking-2507',
    'accounts/fireworks/models/qwen3-coder-480b-a35b-instruct',
    'accounts/fireworks/models/qwen3p5-397b-a17b',
}


def _defaults():
    return json.loads((ROOT / 'core/settings_defaults.json').read_text(encoding='utf-8'))


def _presets():
    return json.loads((ROOT / 'core/provider_presets.json').read_text(encoding='utf-8'))['presets']


def _core_options():
    from core.chat.llm_providers import provider_registry
    return {k: v['model_options'] for k, v in provider_registry._core_providers.items()}


def _all_shipped_ids():
    ids = set()
    for opts in _core_options().values():
        ids |= set(opts)
    for p in _presets().values():
        ids |= {m['id'] for m in p.get('suggested_models', [])}
    d = _defaults()['llm']
    ids |= {k for k in d['MODEL_GENERATION_PROFILES'] if not k.startswith('_')}
    ids |= {p['model'] for p in d['LLM_PROVIDERS'].values()}
    return ids


# ── roster files ────────────────────────────────────────────────────────────

def test_no_dead_ids_anywhere_in_the_roster():
    dead = _all_shipped_ids() & DEAD_IDS
    assert not dead, f"dead model ids shipped: {sorted(dead)}"


def test_core_defaults_are_in_their_own_dropdown():
    opts = _core_options()
    for key, cfg in _defaults()['llm']['LLM_PROVIDERS'].items():
        assert cfg['model'] in opts[key], f"{key} default {cfg['model']!r} not in its dropdown"


def test_current_flagships_are_offered():
    opts = _core_options()
    assert 'claude-opus-5' in opts['claude'] and 'claude-fable-5-1' in opts['claude']
    assert 'claude-opus-5-5' in opts['claude']
    assert 'claude-haiku-4-5' in opts['claude']
    assert 'gpt-6-astra' in opts['openai'] and 'gpt-5.6-terra' in opts['openai']
    assert 'gemini-3.8-flash' in opts['gemini']


def test_fireworks_preset_ids_carry_the_account_prefix():
    for m in _presets()['fireworks']['suggested_models']:
        assert m['id'].startswith('accounts/fireworks/models/'), m['id']


def test_preset_entries_are_well_formed():
    for key, p in _presets().items():
        for m in p.get('suggested_models', []):
            assert set(m) == {'id', 'name'} and m['id'] and m['name'], (key, m)


# ── OpenAI lanes ────────────────────────────────────────────────────────────

def test_every_gpt5_and_gpt6_dropdown_id_rides_the_responses_lane():
    from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
    for mid in _core_options()['openai']:
        expect = mid.startswith(('gpt-5', 'gpt-6'))
        assert OpenAIResponsesProvider.should_use_responses_api(mid) is expect, mid


def _compat(model, base_url='https://api.openai.com/v1'):
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider
    with patch.object(OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = OpenAICompatProvider()
    p.base_url, p.model, p.config = base_url, model, {}
    return p


def test_gpt6_is_a_reasoning_model_on_the_chat_completions_lane():
    out = _compat('gpt-6-astra')._transform_params_for_model(
        {'temperature': 0.7, 'top_p': 0.9, 'max_tokens': 512, 'presence_penalty': 0.1})
    assert 'max_completion_tokens' in out and 'max_tokens' not in out
    assert 'temperature' not in out and 'presence_penalty' not in out


# ── DeepSeek official disable switch (V4 thinks by default) ─────────────────

def test_deepseek_official_disable_thinking_uses_the_v4_switch():
    p = _compat('deepseek-v4-pro', 'https://api.deepseek.com/v1')
    kwargs = {}
    p._inject_thinking_control(kwargs, want_disable=True)
    assert kwargs['extra_body']['thinking'] == {'type': 'disabled'}


# ── Claude thinking switchboard ─────────────────────────────────────────────

def _claude(model, **cfg):
    from core.chat.llm_providers.claude import ClaudeProvider
    return ClaudeProvider({'provider': 'claude', 'api_key': 'test', 'model': model, **cfg})


def _kw(model):
    return {'model': model, 'messages': [], 'max_tokens': 4096}


def test_opus5_on_sends_adaptive_with_effort_and_floors_max_tokens():
    kw = _kw('claude-opus-5')
    strip = _claude('claude-opus-5', reasoning_effort='xhigh')._apply_thinking(kw, True, False)
    assert kw['thinking'] == {'type': 'adaptive', 'display': 'summarized'}
    assert kw['output_config'] == {'effort': 'xhigh'}
    assert kw['max_tokens'] == 16000 and strip is False


@pytest.mark.parametrize('enabled,disable', [(True, True), (False, False)])
def test_opus5_off_is_an_explicit_disabled_not_an_omission(enabled, disable):
    """Opus 5 thinks when the param is omitted — the old omit-to-disable was
    silent billed thinking. Off must be spelled out, and no effort rides
    with it (disabled is only accepted at effort ≤ high)."""
    kw = _kw('claude-opus-5')
    strip = _claude('claude-opus-5', reasoning_effort='max')._apply_thinking(kw, enabled, disable)
    assert kw['thinking'] == {'type': 'disabled'}
    assert 'output_config' not in kw and kw['max_tokens'] == 4096 and strip is True


def test_fable_never_gets_disabled_and_never_strips_history():
    kw = _kw('claude-fable-5-1')
    strip = _claude('claude-fable-5-1')._apply_thinking(kw, True, True)
    assert 'thinking' not in kw and 'output_config' not in kw and strip is False
    kw = _kw('claude-fable-5-1')
    _claude('claude-fable-5-1', reasoning_effort='low')._apply_thinking(kw, True, False)
    assert kw['thinking']['type'] == 'adaptive' and kw['output_config'] == {'effort': 'low'}


def test_haiku_uses_a_budget_below_max_tokens_and_no_effort():
    kw = _kw('claude-haiku-4-5')
    strip = _claude('claude-haiku-4-5', reasoning_effort='high')._apply_thinking(kw, True, False)
    assert kw['thinking']['type'] == 'enabled'
    assert 1024 <= kw['thinking']['budget_tokens'] < kw['max_tokens']
    assert 'output_config' not in kw and 'display' not in kw['thinking'] and strip is False
    kw = _kw('claude-haiku-4-5')
    assert _claude('claude-haiku-4-5')._apply_thinking(kw, True, True) is True
    assert 'thinking' not in kw


def test_probe_kwargs_only_say_disabled_where_the_family_accepts_it():
    assert _claude('claude-opus-5')._probe_kwargs() == {'thinking': {'type': 'disabled'}}
    assert _claude('claude-fable-5-1')._probe_kwargs() == {}
    assert _claude('claude-haiku-4-5')._probe_kwargs() == {}


def test_opus55_rides_the_always_on_contract():
    """Opus 5.5 (2026-09-23) cannot disable thinking: {type:disabled} and
    budget_tokens 400 at every effort. Same lane as Fable — OFF = omit the
    param, never strip history, probes send nothing. A plain roster row
    would have failed its own health check via the adaptive lane."""
    from core.chat.llm_providers.claude import thinking_family
    assert thinking_family('claude-opus-5-5') == 'always'
    assert thinking_family('claude-opus-5') == 'adaptive'
    kw = _kw('claude-opus-5-5')
    assert _claude('claude-opus-5-5')._apply_thinking(kw, True, True) is False
    assert 'thinking' not in kw and 'output_config' not in kw
    kw = _kw('claude-opus-5-5')
    _claude('claude-opus-5-5', reasoning_effort='xhigh')._apply_thinking(kw, True, False)
    assert kw['thinking']['type'] == 'adaptive' and kw['output_config'] == {'effort': 'xhigh'}
    assert _claude('claude-opus-5-5')._probe_kwargs() == {}


def test_request_path_carries_the_switchboard_result():
    """End to end through chat_completion(): the SDK sees an explicit
    disabled on Opus 5 when the Settings checkbox is off."""
    p = _claude('claude-opus-5', thinking_enabled=True, disable_thinking=True)
    p._client = MagicMock()
    p._parse_response = MagicMock(return_value='parsed')
    p.chat_completion([{'role': 'user', 'content': 'hi'}])
    sent = p._client.messages.create.call_args.kwargs
    assert sent['thinking'] == {'type': 'disabled'} and 'output_config' not in sent


# ── retired tool ────────────────────────────────────────────────────────────

def test_ask_claude_is_gone():
    assert not (ROOT / 'functions/ai.py').exists()
    toolsets = (ROOT / 'core/toolsets/toolsets.json').read_text(encoding='utf-8')
    assert 'ask_claude' not in toolsets
