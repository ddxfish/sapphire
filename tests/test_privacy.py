"""Private-chat provider gate tests.

Private chats only reach providers marked local/private-safe — the is_local
checkbox on each model (add/edit form). The address whitelist and
core/privacy.py were demolished 2026-07-11 (the checkbox IS the policy);
the tombstone test pins the whole family of dead names.

Run with: pytest tests/test_privacy.py -v
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Auto-mode gate (get_first_available_provider force_privacy) ────────────

def test_auto_mode_excludes_unchecked_provider_in_private_chat():
    """A provider without is_local is skipped in a private chat BEFORE any
    network touch (the continue fires before health_check)."""
    from core.chat.llm_providers import provider_registry
    cfg = {'cloudy': {'enabled': True, 'provider': 'openai',
                      'base_url': 'https://api.example.com/v1', 'model': 'x'}}
    result = provider_registry.get_first_available_provider(
        cfg, ['cloudy'], 1.0, force_privacy=True)
    assert result is None


def test_auto_mode_allows_checked_local_in_private_chat(monkeypatch):
    """is_local: True in the provider's own config (the checkbox) passes the
    private gate — the jane-qwen case (LAN server, non-loopback URL)."""
    from core.chat.llm_providers import provider_registry

    class FakeProv:
        model = 'fake'
        def health_check(self):
            return True

    monkeypatch.setattr(provider_registry, 'get_provider_by_key',
                        lambda *a, **k: FakeProv())
    cfg = {'janeq': {'enabled': True, 'is_local': True, 'provider': 'openai',
                     'base_url': 'http://192.168.0.206:1234/v1', 'model': 'q'}}
    result = provider_registry.get_first_available_provider(
        cfg, ['janeq'], 1.0, force_privacy=True)
    assert result is not None
    assert result[0] == 'janeq'


def test_auto_mode_no_gate_without_private(monkeypatch):
    """force_privacy=False: unchecked providers are NOT filtered (gate is
    private-chat-only, normal chats unaffected)."""
    from core.chat.llm_providers import provider_registry

    class FakeProv:
        model = 'fake'
        def health_check(self):
            return True

    monkeypatch.setattr(provider_registry, 'get_provider_by_key',
                        lambda *a, **k: FakeProv())
    cfg = {'cloudy': {'enabled': True, 'provider': 'openai',
                      'base_url': 'https://api.example.com/v1', 'model': 'x'}}
    result = provider_registry.get_first_available_provider(
        cfg, ['cloudy'], 1.0, force_privacy=False)
    assert result is not None and result[0] == 'cloudy'


# ─── Tombstone ───────────────────────────────────────────────────────────────

def test_whitelist_and_incognito_fully_removed():
    """[TOMBSTONE] Two demolitions: global PRIVACY_MODE (incognito, 2026-07-10)
    and the address whitelist (2026-07-11). Private is per-chat (private_chat /
    scope_private) gated by the per-model is_local checkbox. None of the dead
    names may resurface in core/ or interfaces/."""
    assert not (PROJECT_ROOT / 'core' / 'privacy.py').exists(), \
        "core/privacy.py resurrected"
    needles = ('is_privacy_mode', 'set_privacy_mode', 'PRIVACY_MODE',
               'is_allowed_endpoint', 'PRIVACY_NETWORK_WHITELIST',
               'privacy_check_whitelist')
    offenders = []
    for base in ('core', 'interfaces'):
        for p in (PROJECT_ROOT / base).rglob('*'):
            if p.suffix not in ('.py', '.js', '.html', '.json'):
                continue
            try:
                text = p.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            # Fork 1A exception (2026-07-16): routes/chat.py reads the
            # ORPHANED START_IN_PRIVACY_MODE key from user settings.json to
            # show the one-time "global privacy mode was removed" upgrade
            # notice. Masking that exact literal keeps every other
            # resurrection of the dead names fatal.
            text = text.replace('START_IN_PRIVACY_MODE', '')
            for needle in needles:
                if needle in text:
                    offenders.append(f"{p.relative_to(PROJECT_ROOT)}: {needle}")
    assert not offenders, "dead privacy names resurfaced:\n" + "\n".join(offenders)


# ═════════════════════════════════════════════════════════════════════════════
# The TRIGGER layer (bug hunt 2026-07-15, scout F6): the registry-level tests
# above prove the gate filters when TOLD to — nothing below existed to prove
# the per-turn code actually TELLS it. Every test here pins a line that, if
# refactored away, silently disarms privacy while the whole suite stays green.
# ═════════════════════════════════════════════════════════════════════════════

from unittest.mock import MagicMock, patch


def _chat_skeleton(chat_settings):
    """LLMChat with only what _select_provider touches (test_bug_hunt pattern)."""
    from core.chat.chat import LLMChat
    with patch.object(LLMChat, '__init__', lambda self: None):
        chat = LLMChat()
    chat._use_new_config = True
    chat.session_manager = MagicMock()
    chat.session_manager.get_chat_settings.return_value = chat_settings
    return chat


def _base_config(monkeypatch, custom=None):
    import config
    monkeypatch.setattr(config, 'LLM_PROVIDERS', {'claude': {'enabled': True}}, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS', custom or {}, raising=False)
    monkeypatch.setattr(config, 'LLM_FALLBACK_ORDER', ['claude'], raising=False)
    monkeypatch.setattr(config, 'LLM_REQUEST_TIMEOUT', 5, raising=False)


def test_pinned_cloud_provider_blocked_before_any_network_touch(monkeypatch):
    """The explicit-provider gate (chat.py) — the MORE common path than auto,
    and previously untested. Block must fire before provider construction."""
    from core.chat import chat as chat_mod
    _base_config(monkeypatch)
    spy = MagicMock()
    monkeypatch.setattr(chat_mod, 'get_provider_by_key', spy)

    chat = _chat_skeleton({'private_chat': True, 'llm_primary': 'claude', 'llm_model': ''})
    with pytest.raises(ConnectionError, match='private'):
        chat._select_provider()
    spy.assert_not_called()


def test_pinned_local_provider_passes_private_gate(monkeypatch):
    from core.chat import chat as chat_mod
    _base_config(monkeypatch, custom={'localbox': {'enabled': True, 'is_local': True}})
    provider = MagicMock()
    provider.health_check.return_value = True
    monkeypatch.setattr(chat_mod, 'get_provider_by_key', MagicMock(return_value=provider))

    chat = _chat_skeleton({'private_chat': True, 'llm_primary': 'localbox', 'llm_model': ''})
    key, prov, model = chat._select_provider()
    assert key == 'localbox'


def test_auto_mode_passes_private_chat_as_force_privacy(monkeypatch):
    """The wiring: all registry-level tests pass force_privacy=True by hand.
    If a refactor drops the kwarg (default False) the whole auto-mode gate
    silently stops firing — this is the assertion that catches it."""
    from core.chat import chat as chat_mod
    _base_config(monkeypatch)
    for flag in (True, False):
        spy = MagicMock(return_value=('lmstudio', MagicMock()))
        monkeypatch.setattr(chat_mod, 'get_first_available_provider', spy)
        chat = _chat_skeleton({'private_chat': flag, 'llm_primary': 'auto', 'llm_model': ''})
        chat._select_provider()
        assert spy.call_args.kwargs['force_privacy'] is flag


def test_privacy_check_internal_error_fails_closed(monkeypatch):
    """chat.py deliberately BLOCKS when the privacy check itself explodes.
    Nothing pinned the direction — a catch-and-continue 'simplification'
    would turn every metadata hiccup into a cloud leak."""
    _base_config(monkeypatch)

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError('metadata exploded')
    monkeypatch.setattr('core.chat.llm_providers.PROVIDER_METADATA', Boom())

    chat = _chat_skeleton({'private_chat': True, 'llm_primary': 'claude', 'llm_model': ''})
    with pytest.raises(ConnectionError, match='blocking provider for safety'):
        chat._select_provider()


def test_tool_gate_decision_table_in_private_chat():
    """function_manager._check_privacy_allowed — v2 CHANGED this table
    ('endpoint' went from whitelist-checked to hard-blocked; None→block is
    the silent-default-class invariant) and no test pinned any row."""
    from core.chat.function_manager import FunctionManager
    with patch.object(FunctionManager, '__init__', lambda self: None):
        fm = FunctionManager()
    fm._is_local_map = {'local_tool': True, 'cloud_tool': False,
                        'endpoint_tool': 'endpoint'}
    fm.set_private_chat(True)
    try:
        assert fm._check_privacy_allowed('local_tool') == (True, None)
        for name in ('cloud_tool', 'endpoint_tool', 'unflagged_tool'):
            allowed, msg = fm._check_privacy_allowed(name)
            assert allowed is False, f"{name} must be blocked in a private chat"
            assert msg
    finally:
        fm.set_private_chat(False)
    assert fm._check_privacy_allowed('cloud_tool') == (True, None)  # gate off = open


def test_provider_add_route_defaults_is_local_false(client, monkeypatch):
    """The is_local checkbox IS the whole privacy policy now. If the add/edit
    route ever defaults it truthy, every private chat opens to the cloud with
    zero symptoms — worst silence class of the 2026-07-15 campaign."""
    from core.settings_manager import settings as live_settings
    c, csrf = client
    captured = {}
    monkeypatch.setattr(live_settings, 'get',
                        lambda key, default=None: {} if key == 'LLM_CUSTOM_PROVIDERS' else (default if default is not None else []))
    monkeypatch.setattr(live_settings, 'set',
                        lambda key, val, persist=True: captured.__setitem__(key, val))

    r = c.post('/api/llm/custom-providers', headers={'X-CSRF-Token': csrf},
               json={'name': 'sandboxbox', 'template': 'openai',
                     'base_url': 'http://127.0.0.1:9/v1'})
    assert r.status_code == 200, r.text
    assert captured['LLM_CUSTOM_PROVIDERS']['sandboxbox']['is_local'] is False

    r = c.post('/api/llm/custom-providers', headers={'X-CSRF-Token': csrf},
               json={'name': 'sandboxlocal', 'template': 'openai',
                     'base_url': 'http://127.0.0.1:9/v1', 'is_local': True})
    assert r.status_code == 200, r.text
    assert captured['LLM_CUSTOM_PROVIDERS']['sandboxlocal']['is_local'] is True
