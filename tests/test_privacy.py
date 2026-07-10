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
            for needle in needles:
                if needle in text:
                    offenders.append(f"{p.relative_to(PROJECT_ROOT)}: {needle}")
    assert not offenders, "dead privacy names resurfaced:\n" + "\n".join(offenders)
