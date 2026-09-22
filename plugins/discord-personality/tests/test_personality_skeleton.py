"""S0 skeleton: the module rule, and one handler firing through a host door."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import discord_personality as dp

PLUGIN_DIR = Path(__file__).absolute().parents[1]
MANIFEST = json.loads((PLUGIN_DIR / 'plugin.json').read_text(encoding='utf-8'))


def test_manifest_shape():
    assert MANIFEST['name'] == 'discord-personality'
    assert MANIFEST['requires_plugins'] == ['discord']
    assert MANIFEST['author'] == 'zeebie-the-zebra'
    hooks = MANIFEST['capabilities']['hooks']
    assert set(hooks) == {'discord_message_observed', 'discord_prompt_context', 'discord_reply_planned',
                          'discord_reply_sent', 'discord_voice_utterance', 'discord_tick'}
    assert set(hooks.values()) == {'hooks.py'}


def test_every_module_has_its_toggle_first_and_only_people_defaults_on():
    settings = MANIFEST['capabilities']['settings']
    toggles = {s['key']: s for s in settings if s['key'].endswith('.enabled')}
    assert set(toggles) == {f'{m}.enabled' for m in dp.MODULES}
    for m in dp.MODULES:
        first = next(s for s in settings if s['key'].startswith(m + '.'))
        assert first['key'] == f'{m}.enabled', m
        assert (PLUGIN_DIR / 'discord_personality' / 'modules' / f'{m}.py').exists(), m
    assert {k for k, s in toggles.items() if s['default']} == {'people.enabled'}


def test_toggles_gate_modules_and_birthdays_needs_people():
    cfg = {'people.enabled': True, 'birthdays.enabled': True, 'reminders.enabled': False,
           'typos.enabled': True, 'presence.enabled': False}
    assert dp.active_modules(cfg) == ['people', 'birthdays', 'typos']
    cfg['people.enabled'] = False
    assert dp.active_modules(cfg) == ['typos']
    assert dp.enabled('birthdays', cfg) is False


def test_dispatch_reaches_only_active_modules_and_isolates_errors(monkeypatch):
    calls = []

    def bad(ev):
        raise RuntimeError('module bug')
    fakes = {
        'people': SimpleNamespace(discord_tick=lambda ev: calls.append(('people', ev))),
        'reminders': SimpleNamespace(discord_tick=bad),
        'presence': SimpleNamespace(discord_tick=lambda ev: calls.append(('presence', ev))),
    }
    monkeypatch.setattr(dp, 'settings', lambda: {'people.enabled': True, 'reminders.enabled': True,
                                                 'presence.enabled': False})
    monkeypatch.setattr(dp, '_module', lambda name: fakes[name])
    ev = SimpleNamespace(metadata={'account': 'alpha'})

    assert dp.dispatch('discord_tick', ev) == ['people']
    assert calls == [('people', ev)]


def test_a_module_handler_fires_through_the_host_door(monkeypatch):
    """host fire → core hook_runner → hooks.py (loaded the way core loads it)
    → dispatch → module. The whole chain, no Discord runtime needed."""
    from core.hooks import hook_runner
    from core.plugin_loader import PluginLoader
    from plugins.discord import hooks_out
    import discord_personality.modules.people as people

    seen = []
    monkeypatch.setattr(people, 'discord_message_observed', lambda ev: seen.append(ev))
    monkeypatch.setattr(dp, 'settings', lambda: {'people.enabled': True})
    handler = PluginLoader._load_handler(None, PLUGIN_DIR, 'hooks.py', 'discord_message_observed')
    assert callable(handler)
    hook_runner.unregister_plugin('discord-personality')
    hook_runner.register('discord_message_observed', handler, priority=60, plugin_name='discord-personality')
    try:
        ev = hooks_out.fire('discord_message_observed', {'account': 'alpha', 'channel_id': 'c1',
                                                         'display_name': 'Alice', 'content': 'hello'})
    finally:
        hook_runner.unregister_plugin('discord-personality')

    assert seen == [ev]
    assert ev.metadata['content'] == 'hello'
    assert isinstance(ev.metadata['api'], hooks_out.DiscordAPI)
    assert ev.chat_private is False


def test_module_off_means_the_door_is_silent(monkeypatch):
    import discord_personality.modules.people as people
    seen = []
    monkeypatch.setattr(people, 'discord_message_observed', lambda ev: seen.append(ev))
    monkeypatch.setattr(dp, 'settings', lambda: {'people.enabled': False})
    assert dp.dispatch('discord_message_observed', SimpleNamespace(metadata={})) == []
    assert seen == []
