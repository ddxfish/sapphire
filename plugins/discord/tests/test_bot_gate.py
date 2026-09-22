"""S4: replies to other bots = on/off + allowlist + a cascade brake (sessions are gone)."""
from types import SimpleNamespace

from plugins.discord.conversation.bot_gate import MAX_CONSECUTIVE, BotGate
from plugins.discord.models.settings import SettingsStore


def _obs(author='peer', bot=True):
    return SimpleNamespace(account_name='alpha', channel_id='c1', author_id=author, author_is_bot=bot)


def _settings(enabled=True, allow=('peer',)):
    store = SettingsStore({'bot': {'enabled': enabled, 'allowlist_ids': allow if isinstance(allow, str) else list(allow)}})
    return store.resolve()


def test_humans_always_pass_and_reset_the_brake():
    gate = BotGate()
    for _ in range(MAX_CONSECUTIVE):
        gate.note_reply('alpha', 'c1', author_is_bot=True)
    assert gate.evaluate(_obs(), _settings())['reason'] == 'bot_cascade_cap'
    assert gate.evaluate(_obs('human', bot=False), _settings(enabled=False))['allowed'] is True
    assert gate.evaluate(_obs(), _settings())['reason'] == 'bot_allowlisted'


def test_off_and_allowlist():
    gate = BotGate()
    assert gate.evaluate(_obs(), _settings(enabled=False))['reason'] == 'bot_interaction_disabled'
    assert gate.evaluate(_obs('stranger'), _settings())['reason'] == 'bot_not_allowlisted'
    assert gate.evaluate(_obs(), _settings(allow='peer, other'))['allowed'] is True     # comma string form


def test_brake_counts_only_bot_triggered_replies():
    gate = BotGate()
    for _ in range(MAX_CONSECUTIVE - 1):
        gate.note_reply('alpha', 'c1', author_is_bot=True)
    gate.note_reply('alpha', 'c1', author_is_bot=False)                 # a human got a reply → reset
    gate.note_reply('alpha', 'c1', author_is_bot=True)
    assert gate.evaluate(_obs(), _settings())['allowed'] is True
    assert gate.evaluate(SimpleNamespace(account_name='alpha', channel_id='c2', author_id='peer', author_is_bot=True),
                         _settings())['allowed'] is True                  # per channel
