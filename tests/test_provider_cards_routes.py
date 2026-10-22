"""Provider cards (2026-10-22, tmp/setup-wizard-plan.md wave 2a): the provider
listings carry a blurb per provider and the providers a DISABLED plugin declares,
so a card can turn the plugin on."""
from types import SimpleNamespace

import core.api_fastapi  # noqa: F401 — the app wires the routes; a route module imported first is a circular import
from core.routes import settings as settings_routes


def test_registry_listing_carries_the_description():
    from core.tts.providers import tts_registry
    from core.stt.providers import stt_registry
    from core.embeddings import embedding_registry
    for reg, key in ((tts_registry, 'none'), (stt_registry, 'none'), (embedding_registry, 'local')):
        entry = next(p for p in reg.get_all() if p['key'] == key)
        assert entry['description'], (reg.system_name, key)


def test_dormant_providers_come_from_disabled_plugins_only(monkeypatch):
    infos = [
        {'name': 'piper', 'enabled': False, 'loaded': False,
         'manifest': {'capabilities': {'providers': {'tts': {
             'key': 'piper', 'display_name': 'Piper', 'is_local': True, 'description': 'fast on a CPU'}}},
             'pip_dependencies': ['piper-tts']}},
        {'name': 'elevenlabs', 'enabled': True, 'loaded': True,            # on: the registry lists it itself
         'manifest': {'capabilities': {'providers': {'tts': {'key': 'elevenlabs'}}}}},
        {'name': 'broken', 'enabled': True, 'loaded': False,               # on but blocked: not a card's problem
         'manifest': {'capabilities': {'providers': {'tts': {'key': 'broken'}}}}},
        {'name': 'already', 'enabled': False, 'loaded': False,             # a key the registry already has
         'manifest': {'capabilities': {'providers': {'tts': {'key': 'kokoro'}}}}},
        {'name': 'stt-only', 'enabled': False, 'loaded': False,
         'manifest': {'capabilities': {'providers': {'stt': {'key': 'whisper_x'}}}}},
    ]
    fake_loader = SimpleNamespace(get_all_plugin_info=lambda: infos,
                                  _check_dependencies=lambda m: list(m.get('pip_dependencies', [])))
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'plugin_loader', fake_loader)
    out = settings_routes._dormant_providers('tts', {'kokoro', 'none', 'elevenlabs'})
    assert [p['key'] for p in out] == ['piper']
    p = out[0]
    assert p['plugin_name'] == 'piper' and p['plugin_enabled'] is False
    assert p['is_local'] is True and p['description'] == 'fast on a CPU'
    assert p['missing_deps'] == ['piper-tts']
    assert [p['key'] for p in settings_routes._dormant_providers('stt', set())] == ['whisper_x']


def test_providers_payload_appends_dormant_after_the_registry(monkeypatch):
    reg = SimpleNamespace(get_active_key=lambda: 'kokoro',
                          get_all=lambda: [{'key': 'kokoro'}, {'key': 'none'}])
    monkeypatch.setattr(settings_routes, '_dormant_providers',
                        lambda system_name, registered: [{'key': 'piper', 'plugin_enabled': False}]
                        if registered == {'kokoro', 'none'} else [])
    out = settings_routes._providers_payload(reg, 'tts')
    assert out['active'] == 'kokoro'
    assert [p['key'] for p in out['providers']] == ['kokoro', 'none', 'piper']
    assert out['providers'][0]['is_active'] is True and out['providers'][1]['is_active'] is False
