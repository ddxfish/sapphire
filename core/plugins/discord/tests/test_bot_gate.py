"""Replies to other bots: the allowlist, "answer any bot", and the cascade brake."""
from types import SimpleNamespace

from plugins.discord.conversation.bot_gate import MAX_CONSECUTIVE, BotGate
from plugins.discord.models.settings import SettingsStore


def _obs(author='peer', bot=True):
    return SimpleNamespace(account_name='alpha', channel_id='c1', author_id=author, author_is_bot=bot)


def _settings(allow_all=False, allow=('peer',)):
    return SettingsStore({'bot': {'allow_all': allow_all, 'allowlist_ids': allow if isinstance(allow, str) else list(allow)}}).resolve()


def test_humans_always_pass_and_reset_the_brake():
    gate = BotGate()
    for _ in range(MAX_CONSECUTIVE):
        gate.note_reply('alpha', 'c1', author_is_bot=True)
    assert gate.evaluate(_obs(), _settings())['reason'] == 'bot_cascade_cap'
    assert gate.evaluate(_obs('human', bot=False), _settings(allow=()))['allowed'] is True
    assert gate.evaluate(_obs(), _settings())['reason'] == 'bot_allowlisted'


def test_allowlist_is_the_switch_and_empty_means_no_bot():
    gate = BotGate()
    assert gate.evaluate(_obs(), _settings(allow=()))['reason'] == 'bot_not_allowlisted'
    assert gate.evaluate(_obs('stranger'), _settings())['reason'] == 'bot_not_allowlisted'
    assert gate.evaluate(_obs(), _settings(allow='peer, other'))['allowed'] is True     # comma string form
    assert gate.evaluate(_obs(), None)['reason'] == 'bot_not_allowlisted'                # no settings = no bots


def test_answer_any_bot_ignores_the_list_but_keeps_the_brake():
    gate = BotGate()
    assert gate.evaluate(_obs('stranger'), _settings(allow_all=True, allow=()))['reason'] == 'bot_allowed_all'
    for _ in range(MAX_CONSECUTIVE):
        gate.note_reply('alpha', 'c1', author_is_bot=True)
    assert gate.evaluate(_obs('stranger'), _settings(allow_all=True, allow=()))['reason'] == 'bot_cascade_cap'


def test_brake_counts_only_bot_triggered_replies():
    gate = BotGate()
    for _ in range(MAX_CONSECUTIVE - 1):
        gate.note_reply('alpha', 'c1', author_is_bot=True)
    gate.note_reply('alpha', 'c1', author_is_bot=False)                  # a human-triggered reply resets it
    assert gate.evaluate(_obs(), _settings())['allowed'] is True
