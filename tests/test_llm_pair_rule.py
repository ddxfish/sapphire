"""ONE pair rule (2026-10-07): a provider key and its model override are one
value, normalized at WRITE time by core.chat.llm_providers.resolve
(normalize_pair / normalize_pair_patch), and every store routes through it.

Before this, seven stores could save a model beside 'auto' — dead at resolve
time (the auto path drops overrides) yet shown as live by every picker — and
the persona merge mixed one persona's model onto another provider's task.
Companion to tests/test_llm_pin_writer.py (the chat funnel's S6 rule).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401
from core.chat.llm_providers.resolve import normalize_pair, normalize_pair_patch

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    import config
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'claude': {'enabled': True, 'model': 'claude-opus-5'}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS',
                        {'lanbox': {'enabled': True, 'is_local': True, 'display_name': 'LAN Box', 'model': 'qwen'}},
                        raising=False)


# ── the rule itself ──────────────────────────────────────────────────────────

def test_normalize_pair():
    assert normalize_pair('', '') == ('auto', '')
    assert normalize_pair(None, 'x') == ('auto', '')
    assert normalize_pair('auto', 'x') == ('auto', '')
    assert normalize_pair('none', 'x') == ('none', '')
    assert normalize_pair(' claude ', ' haiku ') == ('claude', 'haiku')


def test_patch_auto_drops_the_model_in_every_shape():
    assert normalize_pair_patch({}, {'llm_primary': 'auto', 'llm_model': 'x'}) == {'llm_primary': 'auto', 'llm_model': ''}
    assert normalize_pair_patch({'llm_primary': 'auto'}, {'llm_model': 'x'}) == {'llm_model': ''}
    assert normalize_pair_patch({}, {'llm_model': 'x'}) == {'llm_model': ''}
    # a stale dead pin on disk heals when its key is re-saved as auto
    assert normalize_pair_patch({'llm_primary': 'auto', 'llm_model': 'stale'}, {'llm_primary': 'auto'}) \
        == {'llm_primary': 'auto', 'llm_model': ''}


def test_patch_keeps_the_s6_semantics():
    cur = {'llm_primary': 'claude', 'llm_model': 'haiku'}
    assert normalize_pair_patch(cur, {'llm_primary': 'claude'}) == {'llm_primary': 'claude'}            # same key: keep
    assert normalize_pair_patch(cur, {'llm_model': 'opus'}) == {'llm_model': 'opus'}
    assert normalize_pair_patch(cur, {'llm_primary': 'lanbox'}) == {'llm_primary': 'lanbox', 'llm_model': ''}
    assert normalize_pair_patch(cur, {'prompt': 'x'}) == {'prompt': 'x'}
    p = {'llm_model': 'x'}
    normalize_pair_patch({}, p)
    assert p == {'llm_model': 'x'}                                                                   # never mutates


def test_patch_takes_custom_keys():
    assert normalize_pair_patch({'provider': 'auto'}, {'model': 'x'}, key='provider', model_key='model') == {'model': ''}
    assert normalize_pair_patch({'provider': 'claude', 'model': 'h'}, {'provider': 'lanbox'},
                                key='provider', model_key='model') == {'provider': 'lanbox', 'model': ''}


# ── the stores ───────────────────────────────────────────────────────────────

def _store(tmp_path):
    from core.chat.history import ChatSessionManager
    return ChatSessionManager(history_dir=str(tmp_path))


def test_chat_funnel_and_pin_writer_drop_a_model_beside_auto(tmp_path):
    sm = _store(tmp_path)
    sm.create_chat('x', settings={'llm_primary': 'auto'})
    assert sm.set_named_chat_settings('x', {'llm_model': 'ghost-model'})
    assert sm.read_chat_settings('x').get('llm_model', '') == ''
    with patch('core.chat.history.publish'), \
         patch('core.chat.stream_brain.get_override', return_value=None):
        assert sm.set_llm_pin('x', 'auto', 'ghost-model') is True
    assert sm.read_chat_settings('x').get('llm_model', '') == ''


def test_provider_delete_sweep_clears_the_dead_keys_model(tmp_path):
    sm = _store(tmp_path)
    sm.create_chat('x', settings={'llm_primary': 'dead', 'llm_model': 'm'})
    with patch('core.chat.history.publish'):
        assert sm.reset_chat_scope_ref('llm_primary', 'dead', reset_to='auto') == ['x']
    got = sm.read_chat_settings('x')
    assert (got['llm_primary'], got['llm_model']) == ('auto', '')


def test_scheduler_stores_the_pair_on_create_and_update():
    src = (ROOT / 'core/continuity/scheduler.py').read_text(encoding='utf-8')
    create = src[src.index('def create_task('):src.index('def update_task(')]
    assert 'normalize_pair(data.get("provider", "auto"), data.get("model", ""))' in create
    assert '"provider": provider,' in create and '"model": model,' in create
    update = src[src.index('def update_task('):]
    assert 'normalize_pair_patch(task, data, key="provider", model_key="model")' in update[:1600]


def test_persona_clean_settings_drops_a_model_beside_auto():
    from core.personas import persona_manager
    out = persona_manager._clean_settings({'llm_primary': 'auto', 'llm_model': 'x', 'prompt': 'p'})
    assert (out['llm_primary'], out['llm_model']) == ('auto', '')
    out = persona_manager._clean_settings({'llm_primary': 'claude', 'llm_model': 'haiku'})
    assert out['llm_model'] == 'haiku'
    out = persona_manager._clean_settings({'llm_model': 'orphan'})          # no provider at all
    assert out['llm_model'] == ''


def _resolve(task, persona_settings):
    from core.continuity.executor import ContinuityExecutor
    ex = ContinuityExecutor(MagicMock())
    with patch('core.personas.persona_manager') as pm:
        pm.get.return_value = {'name': 'p', 'settings': persona_settings}
        return ex._resolve_persona({'persona': 'p', **task})


def test_persona_merge_is_a_pair_never_field_by_field():
    pair = lambda r: (r['provider'], r['model'])
    cl = {'llm_primary': 'claude', 'llm_model': 'haiku'}
    assert pair(_resolve({'provider': 'auto'}, cl)) == ('claude', 'haiku')              # task defers → whole pair
    assert pair(_resolve({}, cl)) == ('claude', 'haiku')                                # blank defers too
    assert pair(_resolve({'provider': 'lanbox', 'model': ''}, cl)) == ('lanbox', '')     # never another provider's model
    assert pair(_resolve({'provider': 'claude', 'model': ''}, cl)) == ('claude', 'haiku')  # same provider → inherit
    assert pair(_resolve({'provider': 'claude', 'model': 'opus'}, cl)) == ('claude', 'opus')  # the task's own stands
    assert pair(_resolve({'provider': 'auto', 'model': 'ghost'}, {'llm_primary': 'auto', 'llm_model': 'x'})) == ('auto', '')


def test_resident_store_keeps_the_pair_coherent(tmp_path, monkeypatch):
    from plugins.mindpalace.tools import palace_tools as pt
    monkeypatch.setattr(pt, '_db_path', tmp_path / 'mind.db', raising=False)
    monkeypatch.setattr(pt, '_db_initialized', False, raising=False)
    res = lambda: (pt.scope_resident('s')['provider'], pt.scope_resident('s')['model'])
    assert pt.set_scope_resident('s', model='ghost')                 # no provider → a model can't ride
    assert res() == (None, None)
    assert pt.set_scope_resident('s', provider='claude', model='haiku')
    assert res() == ('claude', 'haiku')
    assert pt.set_scope_resident('s', provider='lanbox')             # provider change drops the model
    assert res() == ('lanbox', None)
    assert pt.set_scope_resident('s', model='qwen')
    assert res() == ('lanbox', 'qwen')
    assert pt.set_scope_resident('s', provider='')                   # back to auto → model gone
    assert res() == (None, None)


def test_twilio_inbound_rule_brain_rides_as_a_pair():
    src = (ROOT / 'plugins/twilio-voice/daemon.py').read_text(encoding='utf-8')
    assert 'normalize_pair((task or {}).get("provider"), (task or {}).get("model"))' in src
    assert 'patch["llm_model"] = task["model"]' not in src
