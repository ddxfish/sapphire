"""S4: silent reactions rewritten small — lexicon tone, one roll, cooldown, once per message."""
import random
from types import SimpleNamespace

from plugins.discord.conversation import reactions as rx
from plugins.discord.models.settings import SettingsStore


def _settings(**over):
    store = SettingsStore()
    store.global_overlay.reaction.update({'silent_enabled': True, 'reaction_chance': 100, 'reaction_cooldown_seconds': 0, **over})
    return store.resolve()


def _obs(text='this is amazing!', mid='m1'):
    return SimpleNamespace(account_name='alpha', channel_id='c1', message_id=mid, guild_id='g1', author_id='u1',
                           clean_content=text)


def test_tone_and_emoji():
    assert rx.tone('that was amazing, congrats!') == 'very_positive'
    assert rx.tone('thanks, works now') == 'positive'
    assert rx.tone('lol dead') == 'funny'
    assert rx.tone('ugh this is broken again') == 'negative'
    assert rx.tone('my dog passed away') == 'very_negative'
    assert rx.tone('does anyone know why?') == 'curious'
    assert rx.tone('ok') == '' and rx.tone('') == ''
    assert rx.pick_emoji('congrats!', random.Random(1)) in rx._EMOJIS['very_positive']
    assert rx.pick_emoji('ok') == ''


class FakeTransport:
    loop = None

    def __init__(self):
        self.calls = []

    def add_reaction_sync(self, channel_id, message_id, emoji, account_name=None):
        self.calls.append((channel_id, message_id, emoji, account_name))
        return {'status': 'ok'}


def test_roll_cooldown_and_once_per_message(monkeypatch):
    monkeypatch.setattr(rx.time, 'sleep', lambda s: None)
    monkeypatch.setattr(rx.random, 'uniform', lambda a, b: 0.0)
    svc = rx.Reactions()
    intention = svc.evaluate_silent(_obs(), settings=_settings())
    assert intention and intention.emoji and intention.reason == 'silent_reaction'
    transport = FakeTransport()
    assert svc.execute_silent(intention, transport=transport)['status'] == 'reacted'
    assert transport.calls[0][:3] == ('c1', 'm1', intention.emoji)
    assert svc.evaluate_silent(_obs(), settings=_settings()) is None                     # same message: once
    assert svc.evaluate_silent(_obs(mid='m2'), settings=_settings(reaction_cooldown_seconds=60)) is None   # cooldown
    assert svc.evaluate_silent(_obs(mid='m2'), settings=_settings()) is not None
    assert svc.evaluate_silent(_obs('ok', 'm3'), settings=_settings()) is None            # nothing to react to
    assert svc.evaluate_silent(_obs(mid='m4'), settings=_settings(reaction_chance=0)) is None
    assert svc.evaluate_silent(_obs(mid='m4'), settings=_settings(silent_enabled=False)) is None
