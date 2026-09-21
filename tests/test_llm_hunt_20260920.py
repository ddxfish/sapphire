"""LLM provider mini-hunt fixes — 2026-09-20 (record tmp/model-roster-20260920.md, scout rows
S1/F5, F2/F11, F1, F7, S4/F9, U4/S10, U5). Each test names the row it guards."""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

ROOT = Path(__file__).resolve().parent.parent
LLM_JS = (ROOT / 'interfaces/web/static/views/settings-tabs/llm.js').read_text(encoding='utf-8')


def _status_err(code):
    req = httpx.Request('GET', 'https://x.invalid/v1/models')
    resp = httpx.Response(code, request=req, json={'error': {'message': 'nope'}})
    return openai.APIStatusError('nope', response=resp, body=None)


def _compat(model='m', base_url='https://x.invalid/v1'):
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider
    with patch.object(OpenAICompatProvider, '__init__', lambda self, *a, **kw: None):
        p = OpenAICompatProvider()
    p.base_url, p.model, p.config, p.api_key = base_url, model, {}, 'k'
    p.request_timeout, p.health_check_timeout = 5.0, 1.0
    p._client = MagicMock()
    return p


# ── F2: a refused key is a dead provider, not a reachable one ───────────────

@pytest.mark.parametrize('code', [401, 402, 403])
def test_key_refused_on_probe_is_dead(code):
    p = _compat()
    p._client.models.list.side_effect = _status_err(code)
    assert p.health_check() is False


@pytest.mark.parametrize('code', [500, 429, 404])
def test_other_statuses_stay_reachable(code):
    p = _compat()
    p._client.models.list.side_effect = _status_err(code)
    with patch('core.chat.llm_providers.openai_compat.OpenAI', side_effect=RuntimeError('no')):
        assert p.health_check() is True


def test_responses_lane_key_refused_is_dead():
    from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
    with patch.object(OpenAIResponsesProvider, '__init__', lambda self, *a, **kw: None):
        p = OpenAIResponsesProvider()
    p.base_url, p.health_check_timeout, p._client = 'https://x.invalid/v1', 1.0, MagicMock()
    p._client.models.list.side_effect = _status_err(401)
    assert p.health_check() is False


# ── F11: the /v1 auto-correct runs on the 404 it was written for ────────────

def test_404_without_v1_tries_the_v1_fix_first():
    p = _compat(base_url='http://box:11434')
    p._client.models.list.side_effect = _status_err(404)
    fixed = MagicMock()
    with patch('core.chat.llm_providers.openai_compat.OpenAI', return_value=fixed), \
         patch('core.chat.llm_providers.openai_compat._shared_http_client', return_value=None):
        assert p.health_check() is True
    assert p.base_url == 'http://box:11434/v1' and p._client is fixed


def test_404_with_v1_already_present_stays_reachable_without_rewriting():
    p = _compat(base_url='http://box:11434/v1')
    p._client.models.list.side_effect = _status_err(404)
    assert p.health_check() is True and p.base_url == 'http://box:11434/v1'


# ── F1: the learn-once memory outlives the per-turn provider instance ───────

def test_rejected_params_survive_the_per_turn_rebuild():
    from core.chat.llm_providers import openai_compat as oc
    oc._REJECTED_PARAMS.clear()
    cfg = {'base_url': 'https://x.invalid/v1', 'api_key': 'k', 'model': 'm'}
    a = oc.OpenAICompatProvider(dict(cfg))
    a._rejected_params.add('presence_penalty')
    b = oc.OpenAICompatProvider(dict(cfg))            # next turn's instance
    kw = {'presence_penalty': 0.1, 'temperature': 0.5}
    b._strip_rejected(kw)
    assert 'presence_penalty' not in kw and kw['temperature'] == 0.5
    c = oc.OpenAICompatProvider({**cfg, 'model': 'other'})
    assert 'presence_penalty' not in c._rejected_params
    oc._REJECTED_PARAMS.clear()


# ── S1/F5: the Responses template key can actually build a provider ─────────

def test_template_list_keys_are_all_instantiable():
    from core.chat.llm_providers import provider_registry
    keys = {t['key'] for t in provider_registry.get_templates()}
    assert keys <= set(provider_registry._classes), keys - set(provider_registry._classes)
    assert 'openai_responses' in keys
    assert provider_registry.normalize_template('responses') == 'openai_responses'
    assert provider_registry.normalize_template('openai') == 'openai'


def test_legacy_responses_template_still_builds_a_provider():
    from core.chat.llm_providers import provider_registry
    from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
    cfg = {'legacy': {'template': 'responses', 'enabled': True, 'base_url': 'https://x.invalid/v1',
                      'api_key': 'k', 'model': 'gpt-5.6-terra'}}
    assert isinstance(provider_registry.get_provider_by_key('legacy', cfg, 10.0), OpenAIResponsesProvider)


def test_migration_and_wizard_use_the_class_key():
    import inspect
    from core import settings_manager
    assert "'responses': 'openai_responses'" in inspect.getsource(settings_manager)
    assert '__manual_openai_responses__' in LLM_JS and '__manual_responses__' not in LLM_JS


# ── F7: Auto mode trusts a fresh pass instead of re-probing every turn ───────

def test_auto_mode_reuses_a_healthy_verdict_within_ttl():
    from core.chat.llm_providers import provider_registry
    prov = MagicMock(); prov.health_check.return_value = True
    cfg = {'a': {'enabled': True, 'use_as_fallback': True}}
    cache = {}
    with patch.object(provider_registry, 'get_provider_by_key', return_value=prov):
        assert provider_registry.get_first_available_provider(cfg, ['a'], 10.0, health_cache=cache)[0] == 'a'
        assert provider_registry.get_first_available_provider(cfg, ['a'], 10.0, health_cache=cache)[0] == 'a'
    assert prov.health_check.call_count == 1 and 'a' in cache


def test_auto_mode_never_caches_a_failed_probe():
    from core.chat.llm_providers import provider_registry
    prov = MagicMock(); prov.health_check.return_value = False
    cfg = {'a': {'enabled': True, 'use_as_fallback': True}}
    cache = {}
    with patch.object(provider_registry, 'get_provider_by_key', return_value=prov):
        assert provider_registry.get_first_available_provider(cfg, ['a'], 10.0, health_cache=cache) is None
        assert provider_registry.get_first_available_provider(cfg, ['a'], 10.0, health_cache=cache) is None
    assert prov.health_check.call_count == 2 and cache == {}


def test_chat_auto_path_passes_the_shared_cache():
    # 2026-09-21: the resolver moved to core/chat/llm_providers/resolve.py —
    # ONE module-level cache shared by every lane, not an LLMChat attribute.
    src = (ROOT / 'core/chat/llm_providers/resolve.py').read_text(encoding='utf-8')
    auto = src[src.index('def _auto('):]
    assert "health_cache=_HEALTH_CACHE if health == 'cached' else None" in auto[:900]
    assert 'get_first_available_provider(' not in (ROOT / 'core/chat/chat.py').read_text(encoding='utf-8')


# ── S4/F9: Test Connection tells the truth about the probe budget ───────────

def test_probe_budget_note_tells_the_truth():
    import core.api_fastapi  # noqa: F401 — routes are wired through the app; a bare import is circular
    from core.routes.settings import _probe_budget_note
    assert _probe_budget_note(True, 0.05, 0.3) == ''
    assert 'Auto will skip' in _probe_budget_note(True, 1.2, 0.3)
    assert 'FAILED' in _probe_budget_note(False, 0.1, 10.0)


def test_custom_edit_form_exposes_the_health_timeout():
    assert 'id="${prefix}-timeout"' in LLM_JS
    assert re.search(r"const healthTimeout = parseFloat\(g\('timeout'\)", LLM_JS)


# ── U4/S10 + U5: LLM tab contracts (source tripwires, house pattern) ─────────

def test_drag_reorder_writes_the_tab_snapshot():
    drag = LLM_JS[LLM_JS.index('initProviderDragDrop('):]
    assert "ctx.commit('LLM_FALLBACK_ORDER', order)" in drag[:600]   # U1 (b): via the commit helper now


def test_preset_switch_clears_the_typed_key():
    handler = LLM_JS[LLM_JS.index("g('preset')?.addEventListener('change'"):]
    head = handler[:1200]
    assert "g('key').value = ''" in head and "g('vision').checked = false" in head
    assert '_OPTIONAL_KNOBS.forEach' in head


# ═════════════════════════════════════════════════════════════════════════════
# LHF wave 2 (same day): F4, F6, S6, S15/F10, U2/U7/U8, S12, U15, U9, U6
# ═════════════════════════════════════════════════════════════════════════════

LLM_PROVIDERS_JS = (ROOT / 'interfaces/web/static/shared/llm-providers.js').read_text(encoding='utf-8')
WIZARD_JS = (ROOT / 'interfaces/web/static/core-ui/setup-wizard/tabs/llm.js').read_text(encoding='utf-8')
CHAT_MANAGE_JS = (ROOT / 'interfaces/web/static/views/chat-manage.js').read_text(encoding='utf-8')


def test_typed_key_override_beats_the_stored_credential():           # F4
    from core.chat.llm_providers import provider_registry
    with patch('core.credentials_manager.credentials.get_llm_api_key', return_value='stored-key'):
        assert provider_registry.get_api_key('claude', {'api_key_override': 'typed-key'}) == 'typed-key'
        assert provider_registry.get_api_key('claude', {}) == 'stored-key'


def test_test_routes_ride_the_override_slot():                         # F4
    src = (ROOT / 'core/routes/settings.py').read_text(encoding='utf-8')
    assert src.count("['api_key_override' if field == 'api_key' else field] = body[field]") == 2   # test + test-thinking


def test_responses_autoroute_is_openai_official_only():                # F6
    from core.chat.llm_providers import provider_registry
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider
    from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
    gw = {'gw': {'template': 'openai', 'enabled': True, 'base_url': 'https://litellm.example/v1',
                 'api_key': 'k', 'model': 'gpt-5.5'}}
    p = provider_registry.get_provider_by_key('gw', gw, 10.0)
    assert type(p) is OpenAICompatProvider
    official = {'oa': {'provider': 'openai', 'enabled': True, 'base_url': 'https://api.openai.com/v1',
                       'api_key': 'k', 'model': 'gpt-5.5'}}
    assert isinstance(provider_registry.get_provider_by_key('oa', official, 10.0), OpenAIResponsesProvider)


def test_switch_model_clears_the_per_provider_model_pin():            # S6
    from core.chat.llm_providers import set_active_model, provider_registry
    sm = MagicMock(); sm.set_llm_pin.return_value = True; sm._effective_chat_name.return_value = 'c'
    system = MagicMock(); system.llm_chat.session_manager = sm
    with patch.object(provider_registry, 'get_all_providers',
                      return_value=[{'key': 'claude', 'enabled': True, 'display_name': 'Claude'}]), \
         patch('core.event_bus.publish'):
        ok, msg = set_active_model(system, 'claude')
    assert ok and msg == 'Claude'
    # 2026-09-21: ONE pin writer — the pair rides set_llm_pin (model cleared)
    assert sm.set_llm_pin.call_args.args == ('c', 'claude', '')


def test_provider_map_writes_are_locked():                             # S15/F10
    src = (ROOT / 'core/routes/settings.py').read_text(encoding='utf-8')
    assert src.count('with _PROVIDER_MAP_LOCK:') == 4   # PUT field, reorder, add, delete


def test_custom_model_box_has_one_handler_and_empty_model_is_custom():  # U2 / U7
    assert 'class="model-custom"' in LLM_PROVIDERS_JS and 'provider-field model-custom' not in LLM_PROVIDERS_JS
    assert 'const isCustom = !currentModel || !modelKeys.includes(currentModel);' in LLM_PROVIDERS_JS


def test_other_custom_parks_the_gen_section():                         # U8
    assert "loadModelGenParamsIntoCard(card, '', generationProfiles)" in LLM_JS
    assert 'Pick or type a model first' in LLM_JS


def test_penalties_have_no_silent_default_in_the_card():              # S12
    assert 'presence_penalty: 0.1' not in LLM_PROVIDERS_JS and 'frequency_penalty: 0.1' not in LLM_PROVIDERS_JS
    assert "if (raw !== '' && !isNaN(n)) params[p] = n;" in LLM_PROVIDERS_JS


def test_core_openai_card_has_no_dead_disable_thinking_toggle():      # U15
    assert "const isOpenAITemplate = config.template === 'openai';" in LLM_PROVIDERS_JS


def test_wizard_puts_fail_loudly():                                    # U9
    assert "import { showToast } from '../../../shared/toast.js';" in WIZARD_JS
    assert WIZARD_JS.count('await updateProvider(') == 2 and WIZARD_JS.count('} catch (err) {') >= 2
    assert 'if (!settings.LLM_PROVIDERS) settings.LLM_PROVIDERS = {};' in WIZARD_JS.split('// Config field changes')[1]


def test_compress_modal_forgets_providers_on_change():                # U6
    assert "eventBus.on(eventBus.Events.SETTINGS_CHANGED" in CHAT_MANAGE_JS
    assert "_llmCache = null;" in CHAT_MANAGE_JS.split('eventBus.Events.SETTINGS_CHANGED')[1][:200]
