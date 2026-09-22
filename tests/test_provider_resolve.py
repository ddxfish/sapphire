"""ONE provider resolver — contract suite (2026-09-21).

core/chat/llm_providers/resolve.py replaced six hand-rolled copies of
"pin → running provider". Record: tmp/provider-resolver-20260921.md.
Every lane ports onto resolve() behind these tests; a row here is the
contract each copy used to keep (or drift from) on its own.
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401 — settles the import order for route modules
import core.chat.llm_providers as lp
from core.chat.llm_providers import resolve as rz
from core.chat.llm_providers.resolve import (
    GLOBAL, LLMDisabled, NoProvidersAvailable, PrivacyRefused, ProviderRefused,
    ProviderUnavailable, Selection, resolve)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    rz.clear_health_cache()
    import config
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'claude': {'enabled': True}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS',
                        {'lanbox': {'enabled': True, 'is_local': True, 'display_name': 'LAN Box'}},
                        raising=False)
    monkeypatch.setattr(config, 'LLM_FALLBACK_ORDER', ['claude', 'lanbox'], raising=False)
    monkeypatch.setattr(config, 'LLM_REQUEST_TIMEOUT', 240.0, raising=False)
    yield
    rz.clear_health_cache()


def _prov(model='m', healthy=True, sees=True):
    p = MagicMock()
    p.model = model
    p.health_check.return_value = healthy
    type(p).supports_images = property(lambda self: sees)
    return p


# ── exits ────────────────────────────────────────────────────────────────────

def test_every_refusal_is_a_connection_error():
    for cls in (LLMDisabled, PrivacyRefused, ProviderUnavailable, NoProvidersAvailable):
        assert issubclass(cls, ProviderRefused) and issubclass(cls, ConnectionError)


def test_none_is_loud_everywhere(monkeypatch):
    by_key = MagicMock(); first = MagicMock()
    monkeypatch.setattr(lp, 'get_provider_by_key', by_key)
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    with pytest.raises(LLMDisabled, match='llm_primary=none'):
        resolve('none')
    assert not by_key.called and not first.called


def test_primary_normalizes_blank_to_auto(monkeypatch):
    first = MagicMock(return_value=('claude', _prov()))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    for raw in ('', None, '  '):
        assert resolve(raw).key == 'claude'


# ── prompt gate ──────────────────────────────────────────────────────────────

def test_prompt_gate_blocks_private_prompt_on_public_turn(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock())
    with patch('core.prompts.get_prompt', return_value={'content': 'x', 'privacy_required': True}):
        with pytest.raises(PrivacyRefused, match='marked private'):
            resolve('lanbox', prompt_name='secret')


def test_prompt_gate_runs_before_the_none_sentinel():
    with patch('core.prompts.get_prompt', return_value={'privacy_required': True}):
        with pytest.raises(PrivacyRefused):
            resolve('none', prompt_name='secret')


def test_prompt_gate_passes_when_turn_is_private(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov()))
    with patch('core.prompts.get_prompt', return_value={'privacy_required': True}):
        assert resolve('lanbox', private=True, prompt_name='secret', health='skip').key == 'lanbox'


def test_prompt_gate_global_sentinel_reads_the_active_prompt(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov()))
    with patch('core.prompts.is_current_prompt_private', return_value=True):
        with pytest.raises(PrivacyRefused):
            resolve('lanbox', prompt_name=GLOBAL)
    with patch('core.prompts.is_current_prompt_private', return_value=False):
        assert resolve('lanbox', prompt_name=GLOBAL, health='skip').key == 'lanbox'


def test_prompt_gate_unresolvable_name_is_not_private(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov()))
    with patch('core.prompts.get_prompt', return_value=None):
        assert resolve('lanbox', prompt_name='gone', health='skip').key == 'lanbox'


def test_prompt_gate_internal_error_blocks():
    with patch('core.prompts.get_prompt', side_effect=RuntimeError('boom')):
        with pytest.raises(PrivacyRefused, match='blocking for safety'):
            resolve('lanbox', prompt_name='secret')


def test_no_prompt_name_means_no_gate(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov()))
    with patch('core.prompts.get_prompt', side_effect=AssertionError('must not be called')):
        assert resolve('lanbox', health='skip').key == 'lanbox'


# ── pinned ───────────────────────────────────────────────────────────────────

def test_private_turn_refuses_cloud_pin_before_any_registry_touch(monkeypatch):
    by_key = MagicMock(); monkeypatch.setattr(lp, 'get_provider_by_key', by_key)
    with pytest.raises(PrivacyRefused, match='private'):
        resolve('claude', private=True)
    assert not by_key.called


def test_private_turn_allows_local_pin(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov()))
    assert resolve('lanbox', private=True, health='skip').key == 'lanbox'


def test_is_local_metadata_explosion_fails_closed(monkeypatch):
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError('metadata exploded')
    monkeypatch.setattr(lp, 'PROVIDER_METADATA', Boom())
    with pytest.raises(PrivacyRefused, match='blocking provider for safety'):
        resolve('claude', private=True)


def test_unavailable_pin_raises_and_never_falls_back_to_auto(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=None))
    first = MagicMock(); monkeypatch.setattr(lp, 'get_first_available_provider', first)
    with pytest.raises(ProviderUnavailable, match='not configured or disabled'):
        resolve('claude')
    with pytest.raises(ProviderUnavailable):
        resolve('nosuch')
    assert not first.called


def test_pin_passes_model_override_and_timeout(monkeypatch):
    by_key = MagicMock(return_value=_prov()); monkeypatch.setattr(lp, 'get_provider_by_key', by_key)
    sel = resolve('claude', 'claude-opus-5', timeout=20, health='skip')
    assert sel.model == 'claude-opus-5' and sel.effective_model == 'claude-opus-5'
    assert by_key.call_args.args[2] == 20.0 and by_key.call_args.kwargs['model_override'] == 'claude-opus-5'
    resolve('claude', timeout=0, health='skip')
    assert by_key.call_args.args[2] == 240.0          # 0 / None → config default
    resolve('claude', timeout='junk', health='skip')
    assert by_key.call_args.args[2] == 240.0


def test_pinned_health_verdict_is_cached_and_shared(monkeypatch):
    p = _prov(); monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=p))
    resolve('claude'); resolve('claude')
    assert p.health_check.call_count == 1 and 'claude' in rz._HEALTH_CACHE
    rz._HEALTH_CACHE['claude'] = time.time() - 1            # expired
    resolve('claude')
    assert p.health_check.call_count == 2


def test_pinned_failed_probe_raises_and_is_never_cached(monkeypatch):
    p = _prov(healthy=False); monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=p))
    with pytest.raises(ProviderUnavailable, match='failed health check'):
        resolve('claude')
    assert 'claude' not in rz._HEALTH_CACHE


def test_health_modes(monkeypatch):
    p = _prov(); monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=p))
    resolve('claude', health='skip'); resolve('claude', health='skip')
    assert p.health_check.call_count == 0
    resolve('claude', health='probe'); resolve('claude', health='probe')
    assert p.health_check.call_count == 2


def test_pinned_blind_provider_refused_when_images_required(monkeypatch):
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov(sees=False)))
    with pytest.raises(ProviderUnavailable, match='does not support images'):
        resolve('claude', require_images=True, health='skip')
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov(sees=True)))
    assert resolve('claude', require_images=True, health='skip').key == 'claude'


# ── auto ─────────────────────────────────────────────────────────────────────

def test_auto_passes_privacy_cache_timeout_and_drops_the_override(monkeypatch):
    first = MagicMock(return_value=('claude', _prov('default-m')))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    for flag in (True, False):
        sel = resolve('auto', 'stale-model', private=flag, timeout=20)
        kw = first.call_args.kwargs
        assert kw['force_privacy'] is flag
        assert kw['health_cache'] is rz._HEALTH_CACHE
        assert first.call_args.args[2] == 20.0            # per-chat timeout reaches auto too
        assert sel.model == '' and sel.effective_model == 'default-m'


def test_auto_skip_health_passes_probe_false(monkeypatch):
    first = MagicMock(return_value=('claude', _prov()))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    resolve('auto', health='skip', require_images=True)
    kw = first.call_args.kwargs
    assert kw['probe'] is False and kw['health_cache'] is None and kw['require_images'] is True


def test_auto_nothing_available_raises(monkeypatch):
    monkeypatch.setattr(lp, 'get_first_available_provider', MagicMock(return_value=None))
    with pytest.raises(NoProvidersAvailable, match='No LLM providers available'):
        resolve('auto')
    with pytest.raises(NoProvidersAvailable, match='marked local'):
        resolve('auto', private=True)


def test_auto_uses_fallback_order_or_the_map(monkeypatch):
    first = MagicMock(return_value=('claude', _prov()))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    resolve('auto')
    assert first.call_args.args[1] == ['claude', 'lanbox']
    resolve('auto', fallback_order=['lanbox'])
    assert first.call_args.args[1] == ['lanbox']


# ── registry extensions the auto path relies on ──────────────────────────────

def test_registry_scan_skips_blind_candidates_when_images_required():
    blind, sighted = _prov(sees=False), _prov(sees=True)
    cfg = {'b': {'enabled': True}, 's': {'enabled': True}}
    with patch.object(lp.provider_registry, 'get_provider_by_key',
                      side_effect=lambda k, *a, **kw: {'b': blind, 's': sighted}[k]):
        assert lp.provider_registry.get_first_available_provider(cfg, ['b', 's'], 10.0, require_images=True)[0] == 's'
        assert lp.provider_registry.get_first_available_provider(cfg, ['b', 's'], 10.0)[0] == 'b'


def test_registry_scan_probe_false_never_health_checks():
    p = _prov(healthy=False)
    cfg = {'a': {'enabled': True}}
    with patch.object(lp.provider_registry, 'get_provider_by_key', return_value=p):
        assert lp.provider_registry.get_first_available_provider(cfg, ['a'], 10.0, probe=False)[0] == 'a'
        assert lp.provider_registry.get_first_available_provider(cfg, ['a'], 10.0) is None
    assert p.health_check.call_count == 1


# ── helpers ──────────────────────────────────────────────────────────────────

def test_is_local_and_display_name():
    assert rz.is_local('lanbox') is True and rz.is_local('claude') is False and rz.is_local('nope') is False
    assert rz.display_name('lanbox') == 'LAN Box' and rz.display_name('nope') == 'nope'
    assert rz.display_name('auto') == 'auto' and rz.display_name('') == 'auto'
    assert rz.providers_config()['lanbox']['is_local'] is True


def test_selection_effective_model():
    p = _prov('provider-default')
    assert Selection('k', p, '').effective_model == 'provider-default'
    assert Selection('k', p, 'pin').effective_model == 'pin'
    assert Selection('lanbox', p).display_name == 'LAN Box'


# ── the web-chat door is a thin wrapper ──────────────────────────────────────

def _chat_skeleton(chat_settings):
    from core.chat.chat import LLMChat
    with patch.object(LLMChat, '__init__', lambda self: None):
        chat = LLMChat()
    chat.session_manager = MagicMock()
    chat.session_manager.get_chat_settings.return_value = chat_settings
    return chat


def test_chat_select_provider_hands_its_settings_to_resolve(monkeypatch):
    seen = {}

    def fake(primary, model='', **kw):
        seen.update(primary=primary, model=model, **kw)
        return Selection(primary, _prov(), model)
    monkeypatch.setattr(rz, 'resolve', fake)
    chat = _chat_skeleton({'llm_primary': 'lanbox', 'llm_model': 'q', 'private_chat': True,
                           'prompt': 'secret', 'llm_request_timeout': '20'})
    assert chat._select_provider()[0] == 'lanbox' and chat._select_provider()[2] == 'q'
    assert seen['private'] is True and seen['prompt_name'] == 'secret' and seen['timeout'] == 20.0
    chat = _chat_skeleton({'llm_primary': 'auto'})
    chat._select_provider()
    assert seen['prompt_name'] is GLOBAL and seen['timeout'] == 0.0


def test_chat_py_carries_no_copy_of_the_resolver():
    src = (ROOT / 'core/chat/chat.py').read_text(encoding='utf-8')
    for needle in ('get_first_available_provider(', 'get_provider_by_key(', '_pinned_health_cache',
                   'PROVIDER_METADATA'):
        assert needle not in src, needle


# ═════════════════════════════════════════════════════════════════════════════
# WAVE 2 — the ported lanes, behaviorally (not tripwires). Each lane hands its
# own pin + privacy answer to resolve() and never re-implements the rule.
# ═════════════════════════════════════════════════════════════════════════════

def _ctx(requires_privacy, task_settings):
    from core.continuity.execution_context import ExecutionContext
    ctx = ExecutionContext.__new__(ExecutionContext)
    ctx.task_settings = task_settings
    ctx._prompt_privacy_required = requires_privacy
    return ctx


def test_continuity_auto_drops_the_task_model_and_passes_the_chat_timeout(monkeypatch):
    first = MagicMock(return_value=('claude', _prov('claude-default')))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    ctx = _ctx(False, {'prompt': 'p', 'provider': 'auto', 'model': 'stale-qwen', 'llm_request_timeout': 20})
    key, prov, override = ctx._resolve_provider()
    assert key == 'claude' and override == ''                      # V4: no stale pin rides to auto's pick
    assert first.call_args.args[2] == 20.0                          # V5/D6: chat timeout reaches continuity
    assert first.call_args.kwargs['force_privacy'] is False


def test_continuity_pinned_carries_model_and_task_privacy(monkeypatch):
    by_key = MagicMock(return_value=_prov()); monkeypatch.setattr(lp, 'get_provider_by_key', by_key)
    key, prov, override = _ctx(True, {'prompt': 'p', 'provider': 'lanbox', 'model': 'q'})._resolve_provider()
    assert (key, override) == ('lanbox', 'q') and by_key.call_args.kwargs['model_override'] == 'q'
    with pytest.raises(ConnectionError, match='requires privacy'):
        _ctx(True, {'prompt': 'p', 'provider': 'claude', 'model': ''})._resolve_provider()


def test_continuity_event_payload_override_is_retired(monkeypatch):
    # S0 doors 2026-09-21: the task's provider IS the brain. A daemon payload
    # carrying llm_primary/llm_model (Discord's Reply LLM) no longer beats the
    # task's dropdown — that override was the 1pm "good morning!" class.
    from core.continuity import executor as ex
    by_key = MagicMock(return_value=_prov()); monkeypatch.setattr(lp, 'get_provider_by_key', by_key)
    token = ex.current_event_data.set({'llm_primary': 'lanbox', 'llm_model': 'ev-model'})
    try:
        key, _, override = _ctx(False, {'prompt': 'p', 'provider': 'claude', 'model': 'task-model'})._resolve_provider()
    finally:
        ex.current_event_data.reset(token)
    assert (key, override) == ('claude', 'task-model')


def test_game_seat_never_reads_the_operators_web_chat(monkeypatch):
    import sys
    plugin_dir = str(ROOT / 'plugins' / 'game-room')
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    import gameroom_core
    import core.api_fastapi as api
    system = MagicMock()
    monkeypatch.setattr(api, 'get_system', lambda: system)
    first = MagicMock(return_value=('claude', _prov()))
    monkeypatch.setattr(lp, 'get_first_available_provider', first)
    assert gameroom_core._get_provider(None) is first.return_value[1]
    assert not system.llm_chat._select_provider.called
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=_prov('qwen')))
    info = gameroom_core.provider_info('lanbox', '')                 # dry run: friendly name + effective model
    assert info == {'provider': 'lanbox', 'display_name': 'LAN Box', 'model': 'qwen'}
    # a dead pin is terminal — no auto fallback, no web chat
    monkeypatch.setattr(lp, 'get_provider_by_key', MagicMock(return_value=None))
    first.reset_mock()
    assert gameroom_core._get_provider('claude') is None
    assert not first.called and not system.llm_chat._select_provider.called
