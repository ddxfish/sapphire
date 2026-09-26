"""S4: silent reactions rewritten small — lexicon tone, one roll, cooldown, once per message."""
import random
from types import SimpleNamespace

from plugins.discord.conversation import reactions as rx
from plugins.discord.models.settings import SettingsStore


def _settings(**over):
    store = SettingsStore({'reaction': {'silent_enabled': True, 'reaction_chance': 100, 'reaction_cooldown_seconds': 0, **over}})
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


def test_reacted_messages_memory_is_bounded():
    from plugins.discord.models.intentions import AddReactionIntention
    service = rx.Reactions()
    for i in range(rx.REACTED_MESSAGES_CAP + 50):
        intention = AddReactionIntention(intention_type='add_reaction', account_name='bot', channel_id='c',
                                         message_id=str(i), reason='r', emoji='x')
        service._record(intention, result={}, delay=0.0)
    assert len(service._reacted_messages) == rx.REACTED_MESSAGES_CAP
    assert ('bot', 'c', '0') not in service._reacted_messages


# ── tone engine: RoBERTa (2026-09-26) ────────────────────────────────────────
from plugins.discord.conversation import sentiment as st   # noqa: E402


def test_roberta_engine_scores_then_falls_to_the_word_list(monkeypatch):
    scores = {'great stuff': 0.8, 'meh not good': -0.3, 'does anyone know why?': 0.0, 'lol dead': -0.9}
    monkeypatch.setattr(st, 'compound', lambda t: scores.get(t))
    assert rx.tone('great stuff', 'roberta') == 'very_positive'
    assert rx.tone('meh not good', 'roberta') == 'negative'
    assert rx.tone('does anyone know why?', 'roberta') == 'curious'       # neutral → the word list's rules
    assert rx.tone('lol dead', 'roberta') == 'funny'                      # funny is the word list's; model skipped
    monkeypatch.setattr(st, 'compound', lambda t: None)                   # model unavailable → word list
    assert rx.tone('thanks, works now', 'roberta') == 'positive'
    assert rx.tone('what a day') == '' and rx.tone('what a day', 'roberta') == ''   # default engine never asks the model
    assert (rx._tier_from_compound(0.5), rx._tier_from_compound(0.1), rx._tier_from_compound(0.05),
            rx._tier_from_compound(-0.1), rx._tier_from_compound(-0.5)) == \
           ('very_positive', 'positive', '', 'negative', 'very_negative')


def test_roberta_scores_at_execute_time_not_on_evaluate(monkeypatch):
    monkeypatch.setattr(rx.time, 'sleep', lambda s: None)
    monkeypatch.setattr(rx.random, 'uniform', lambda a, b: 0.0)
    calls = []
    monkeypatch.setattr(st, 'compound', lambda t: calls.append(t) or 0.9)
    svc = rx.Reactions()
    intention = svc.evaluate_silent(_obs('what a day'), settings=_settings(sentiment_engine='roberta'))
    assert intention and intention.emoji == '' and intention.text == 'what a day' and calls == []
    transport = FakeTransport()
    assert svc.execute_silent(intention, transport=transport)['status'] == 'reacted'
    assert calls == ['what a day'] and intention.text == ''               # scored once, text dropped
    assert transport.calls[0][2] == intention.emoji and intention.emoji in rx._EMOJIS['very_positive']


def test_roberta_neutral_message_is_skipped_without_recording(monkeypatch):
    monkeypatch.setattr(rx.time, 'sleep', lambda s: None)
    monkeypatch.setattr(st, 'compound', lambda t: 0.0)
    svc = rx.Reactions()
    intention = svc.evaluate_silent(_obs('ok then', 'm9'), settings=_settings(sentiment_engine='roberta'))
    assert intention is not None
    transport = FakeTransport()
    assert svc.execute_silent(intention, transport=transport) == {'status': 'skipped', 'reason': 'no_tone'}
    assert transport.calls == [] and not svc._reacted_messages and not svc._last_reaction_at


def test_roberta_async_path_scores_in_a_thread(monkeypatch):
    import asyncio
    monkeypatch.setattr(st, 'compound', lambda t: 0.9)
    monkeypatch.setattr(rx.random, 'uniform', lambda a, b: 0.0)
    svc = rx.Reactions()
    intention = svc.evaluate_silent(_obs('brilliant work', 'm10'), settings=_settings(sentiment_engine='roberta'))

    class LoopTransport(FakeTransport):
        async def add_reaction_async(self, channel_id, message_id, emoji, account_name=None):
            self.calls.append((channel_id, message_id, emoji, account_name))
            return {'status': 'ok'}

    async def run():
        t = LoopTransport()
        t.loop = asyncio.get_running_loop()
        assert svc.execute_silent(intention, transport=t)['status'] == 'scheduled'
        for _ in range(100):
            if t.calls:
                break
            await asyncio.sleep(0.02)
        return t.calls

    calls = asyncio.run(run())
    assert calls and calls[0][2] in rx._EMOJIS['very_positive'] and intention.text == ''


def test_tone_engine_setting_defaults_to_the_word_list():
    import json
    from pathlib import Path
    assert _settings().reaction.sentiment_engine == 'lexicon'
    m = json.loads((Path(rx.__file__).resolve().parents[1] / 'plugin.json').read_text(encoding='utf-8'))
    entry = next(s for s in m['capabilities']['settings'] if s['key'] == 'reaction.sentiment_engine')
    assert entry['default'] == 'lexicon' and {o['value'] for o in entry['options']} == set(rx.ENGINES)
    assert m['version'] == '2.2.0'
    assert not any(d.startswith(('vader', 'transformers', 'torch')) for d in m['pip_dependencies'])   # no new deps


def test_sentiment_module_is_quiet_when_the_model_is_missing(monkeypatch):
    monkeypatch.setattr(st, '_pipe', None)
    monkeypatch.setattr(st, '_failed', True)
    assert st.compound('anything') is None and st.loaded() is False
