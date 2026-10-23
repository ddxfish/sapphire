"""ONE pin writer + the S6 rule in the funnels + the display readers (2026-09-21).

Companion to tests/test_provider_resolve.py. Record: tmp/provider-resolver-20260921.md.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.api_fastapi  # noqa: F401
from core.chat.history import normalize_llm_pin

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    import config
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'claude': {'enabled': True, 'model': 'claude-opus-5'}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS',
                        {'lanbox': {'enabled': True, 'is_local': True, 'display_name': 'LAN Box', 'model': 'qwen'}},
                        raising=False)


# ── the S6 rule, once, in the funnel ─────────────────────────────────────────

def test_normalize_clears_model_on_key_change_only():
    cur = {'llm_primary': 'claude', 'llm_model': 'claude-haiku-4-5'}
    assert normalize_llm_pin(cur, {'llm_primary': 'lanbox'}) == {'llm_primary': 'lanbox', 'llm_model': ''}
    assert normalize_llm_pin(cur, {'llm_primary': 'claude'}) == {'llm_primary': 'claude'}          # same key: keep
    assert normalize_llm_pin(cur, {'llm_primary': 'lanbox', 'llm_model': 'q'}) == {'llm_primary': 'lanbox', 'llm_model': 'q'}
    assert normalize_llm_pin(cur, {'prompt': 'x'}) == {'prompt': 'x'}
    assert normalize_llm_pin({}, {'llm_primary': 'auto'}) == {'llm_primary': 'auto'}               # auto→auto
    patch_ = {'llm_primary': 'lanbox'}
    normalize_llm_pin(cur, patch_)
    assert 'llm_model' not in patch_                                                            # never mutates


def _sm():
    from core.chat.history import ChatSessionManager
    sm = ChatSessionManager.__new__(ChatSessionManager)
    sm.update_chat_settings = MagicMock(return_value=True)
    sm.set_named_chat_settings = MagicMock(return_value=True)
    sm._effective_chat_name = MagicMock(return_value='live')
    return sm


def test_set_llm_pin_writes_the_pair_and_publishes_with_origin():
    sm = _sm()
    with patch('core.chat.history.publish') as pub:
        assert sm.set_llm_pin('live', 'lanbox', origin='tab-1') is True
        assert sm.set_llm_pin('other', 'claude', 'claude-haiku-4-5', origin=None) is True
    sm.update_chat_settings.assert_called_once_with({'llm_primary': 'lanbox', 'llm_model': ''})
    sm.set_named_chat_settings.assert_called_once_with('other', {'llm_primary': 'claude', 'llm_model': 'claude-haiku-4-5'})
    assert pub.call_args_list[0].args[1] == {'chat': 'live', 'settings': {'llm_primary': 'lanbox', 'llm_model': ''}, 'origin': 'tab-1'}


def test_set_llm_pin_refuses_unknown_key_and_accepts_sentinels():
    sm = _sm()
    with patch('core.chat.history.publish'):
        assert sm.set_llm_pin('live', 'nosuch') is False
        assert sm.set_llm_pin('live', 'none') is True and sm.set_llm_pin('live', '') is True
    assert not sm.set_named_chat_settings.called
    assert sm.update_chat_settings.call_args_list[-1].args[0] == {'llm_primary': 'auto', 'llm_model': ''}


def test_set_llm_pin_reports_a_failed_write_and_publishes_nothing():
    sm = _sm(); sm.update_chat_settings.return_value = False
    with patch('core.chat.history.publish') as pub:
        assert sm.set_llm_pin('live', 'claude') is False
    assert not pub.called


# ── the browser door validates + normalizes before it publishes ──────────────

def test_put_route_refuses_a_dead_key_and_normalizes_the_pair(client, mock_system):
    client, csrf = client
    hdr = {'X-CSRF-Token': csrf}
    sm = mock_system.llm_chat.session_manager
    sm.read_chat_settings.return_value = {'llm_primary': 'claude', 'llm_model': 'old'}
    sm.get_active_chat_name.return_value = 'elsewhere'
    seen = {}
    sm.set_named_chat_settings.side_effect = lambda name, patch, **kw: seen.update(name=name, patch=patch) or True
    r = client.put('/api/chats/somechat/settings', headers=hdr, json={'settings': {'llm_primary': 'ghost-provider'}})
    assert r.status_code == 400 and 'no longer exists' in r.json()['detail']
    r = client.put('/api/chats/somechat/settings', headers=hdr, json={'settings': {'llm_primary': 'lanbox'}})
    assert r.status_code == 200
    assert seen['patch'] == {'llm_primary': 'lanbox', 'llm_model': ''}


# ── the twilio outbound line gets the same deadline as inbound ───────────────

def test_twilio_outbound_stamps_the_call_timeout():
    src = (ROOT / 'plugins/twilio-voice/daemon.py').read_text(encoding='utf-8')
    body = src[src.index('def _setup_outbound_chat('):src.index('def ', src.index('def _setup_outbound_chat(') + 10)]
    assert 'patch["llm_request_timeout"] = _to' in body


# ── continuity: provider check + the S6 rule for tasks ───────────────────────

def test_scheduler_refuses_unknown_provider_and_clears_model_on_change(tmp_path):
    from core.continuity.scheduler import ContinuityScheduler
    sched = ContinuityScheduler.__new__(ContinuityScheduler)
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        sched._check_llm_provider({'provider': 'ghost'})
    for ok in ({'provider': 'auto'}, {'provider': 'none'}, {'provider': 'lanbox'}, {'name': 'x'}):
        sched._check_llm_provider(ok)
    src = (ROOT / 'core/continuity/scheduler.py').read_text(encoding='utf-8')
    upd = src[src.index('def update_task('):]
    assert 'self._check_llm_provider(data)' in upd[:1200] and 'data = {**data, "model": ""}' in upd[:1600]


def test_task_routes_map_value_errors_to_400():
    src = (ROOT / 'core/routes/system.py').read_text(encoding='utf-8')
    assert src.count('except ValueError as e:\n        raise HTTPException(status_code=400, detail=str(e))') >= 2   # create + update


# ── provider delete sweeps every pin ─────────────────────────────────────────

def test_delete_sweep_resets_chats_tasks_and_personas(monkeypatch):
    from core.routes import settings as rs
    sm = MagicMock(); sm.reset_chat_scope_ref.return_value = ['a', 'b']
    sched = MagicMock(); sched.list_tasks.return_value = [{'id': 't1', 'provider': 'dead'}, {'id': 't2', 'provider': 'auto'}]
    system = SimpleNamespace(llm_chat=SimpleNamespace(session_manager=sm), continuity_scheduler=sched)
    monkeypatch.setattr(rs, 'get_system', lambda: system)
    from core.personas import persona_manager
    monkeypatch.setattr(persona_manager, 'get_all', lambda: {'p1': {'settings': {'llm_primary': 'dead', 'llm_model': 'm'}},
                                                             'p2': {'settings': {'llm_primary': 'claude'}}})
    upd = MagicMock(return_value=True); monkeypatch.setattr(persona_manager, 'update', upd)
    swept = rs._sweep_llm_pin('dead')
    assert swept == {'chats': 2, 'tasks': 1, 'personas': 1}
    sm.reset_chat_scope_ref.assert_called_once_with('llm_primary', 'dead', reset_to='auto')
    sched.update_task.assert_called_once_with('t1', {'provider': 'auto', 'model': ''})
    assert upd.call_args.args == ('p1', {'settings': {'llm_primary': 'auto', 'llm_model': ''}})


def test_delete_sweep_survives_a_broken_store(monkeypatch):
    from core.routes import settings as rs
    monkeypatch.setattr(rs, 'get_system', lambda: (_ for _ in ()).throw(RuntimeError('down')))
    assert rs._sweep_llm_pin('dead') == {'chats': 0, 'tasks': 0, 'personas': 0}


# ── what she sees ────────────────────────────────────────────────────────────

def test_status_display_is_friendly_name_and_effective_model():
    import importlib.util
    spec = importlib.util.spec_from_file_location('status_routes', ROOT / 'plugins/status/routes/status.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod._llm_display('lanbox', '') == {'llm_display': 'LAN Box', 'llm_effective_model': 'qwen'}
    assert mod._llm_display('lanbox', 'qwen-big') == {'llm_display': 'LAN Box', 'llm_effective_model': 'qwen-big'}
    assert mod._llm_display('claude', '') == {'llm_display': 'Claude', 'llm_effective_model': 'claude-opus-5'}
    assert mod._llm_display('auto', '') == {'llm_display': 'auto', 'llm_effective_model': ''}
    assert mod._llm_display('ghost', '') == {'llm_display': 'ghost', 'llm_effective_model': ''}
    tool = (ROOT / 'plugins/status/tools/status_tool.py').read_text(encoding='utf-8')
    assert "s.get('llm_display')" in tool and "s.get('llm_effective_model')" in tool


def test_persona_load_publishes_the_pin():
    src = (ROOT / 'core/routes/content.py').read_text(encoding='utf-8')
    assert '**{k: settings[k] for k in ("llm_primary", "llm_model") if k in settings}' in src


# ── tripwires: the copies are gone ───────────────────────────────────────────

def test_no_resolver_copies_remain():
    checks = {
        'core/chat/chat.py': ['get_first_available_provider(', 'PROVIDER_METADATA'],
        'core/continuity/execution_context.py': ['get_first_available_provider(', 'PROVIDER_METADATA'],
        'plugins/game-room/gameroom_core.py': ['system.llm_chat._select_provider()', 'get_first_available_provider', '_is_local_provider(key):\n    """Mirror'],
        'core/chat/compress.py': ['get_provider_by_key'],
        'core/routes/chat.py': ['PROVIDER_METADATA'],
        'interfaces/web/static/features/scene.js': ["['lmstudio', 'ollama']"],
    }
    for rel, needles in checks.items():
        src = (ROOT / rel).read_text(encoding='utf-8')
        for n in needles:
            assert n not in src, f'{rel}: {n}'
    assert 'normalize_llm_pin(' in (ROOT / 'core/chat/history.py').read_text(encoding='utf-8')
    assert 'sm.set_llm_pin(' in (ROOT / 'core/chat/llm_providers/__init__.py').read_text(encoding='utf-8')


# ═════════════════════════════════════════════════════════════════════════════
# WAVE 2 — the real store funnels + the Twilio outbound patch, behaviorally.
# ═════════════════════════════════════════════════════════════════════════════

def _store(tmp_path):
    from core.chat.history import ChatSessionManager
    return ChatSessionManager(history_dir=str(tmp_path))


def test_named_funnel_clears_the_model_on_a_key_change(tmp_path):
    sm = _store(tmp_path)
    sm.create_chat('x', settings={'llm_primary': 'claude', 'llm_model': 'claude-haiku-4-5'})
    assert sm.set_named_chat_settings('x', {'llm_primary': 'lanbox'})
    got = sm.read_chat_settings('x')
    assert (got['llm_primary'], got['llm_model']) == ('lanbox', '')
    assert sm.set_named_chat_settings('x', {'llm_primary': 'lanbox', 'llm_model': 'q'})
    assert sm.read_chat_settings('x')['llm_model'] == 'q'
    assert sm.set_named_chat_settings('x', {'llm_primary': 'lanbox'})          # same key → keep
    assert sm.read_chat_settings('x')['llm_model'] == 'q'


def test_active_funnel_clears_the_model_on_a_key_change(tmp_path):
    sm = _store(tmp_path)
    sm.create_chat('y', settings={'llm_primary': 'claude', 'llm_model': 'claude-haiku-4-5'})
    assert sm.set_active_chat('y')
    with patch('core.chat.stream_brain.get_override', return_value=None):
        assert sm.update_chat_settings({'llm_primary': 'lanbox'})
    assert (sm.current_settings['llm_primary'], sm.current_settings['llm_model']) == ('lanbox', '')
    assert sm.read_chat_settings('y')['llm_model'] == ''


def test_set_llm_pin_on_the_real_store(tmp_path):
    sm = _store(tmp_path)
    sm.create_chat('z', settings={'llm_primary': 'claude', 'llm_model': 'old'})
    with patch('core.chat.history.publish') as pub, \
         patch('core.chat.stream_brain.get_override', return_value=None):
        assert sm.set_llm_pin('z', 'lanbox', origin='tab') is True
        assert sm.set_llm_pin('z', 'ghost') is False
    got = sm.read_chat_settings('z')
    assert (got['llm_primary'], got['llm_model']) == ('lanbox', '')
    assert pub.call_count == 1 and pub.call_args.args[1]['origin'] == 'tab'


def test_twilio_outbound_patch_carries_provider_and_timeout(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('twilio_daemon_pin_test', ROOT / 'plugins/twilio-voice/daemon.py')
    daemon = importlib.util.module_from_spec(spec); spec.loader.exec_module(daemon)
    from core.credentials_manager import credentials
    monkeypatch.setattr(credentials, 'get_twilio_account',
                        lambda scope: {'call_provider': 'acct-prov', 'call_model': 'acct-model'})
    system = MagicMock()
    system.llm_chat.list_chats.return_value = []
    system.llm_chat.session_manager.read_chat_settings.return_value = {'prompt': 'sapphire', 'llm_primary': 'claude'}
    calls = {}
    system.llm_chat.session_manager.set_named_chat_settings.side_effect = \
        lambda name, patch: calls.update(name=name, patch=patch) or True
    # per-call pick: key only — the funnel clears the model at write time
    assert daemon._setup_outbound_chat(system, 'acct1', '+15551234567', None, 'default', provider='per-call')
    assert calls['patch']['llm_primary'] == 'per-call' and 'llm_model' not in calls['patch']
    assert calls['patch']['llm_request_timeout'] == 20.0                 # V5: same deadline as inbound
    # no pick: the account's pair
    assert daemon._setup_outbound_chat(system, 'acct1', '+15551234567', None, 'default')
    assert (calls['patch']['llm_primary'], calls['patch']['llm_model']) == ('acct-prov', 'acct-model')
