"""Ignored channel list matching."""

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.conversation.trigger_service import evaluate_reply_trigger
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


def _obs(**kwargs):
    base = dict(
        observation_id='obs-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id='m1',
        content='hello',
        clean_content='hello',
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        author_is_bot=False,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def test_is_channel_ignored_matches_account_channel():
    store = SettingsStore()
    store.global_overlay.channel.update({'ignored_channels': ['alpha:c1', 'beta:c9']})
    settings = store.resolve()
    assert is_channel_ignored('alpha', 'c1', settings) is True
    assert is_channel_ignored('alpha', 'c2', settings) is False
    assert is_channel_ignored('beta', 'c1', settings) is False


def test_is_channel_ignored_accepts_bare_channel_id():
    store = SettingsStore()
    store.global_overlay.channel.update({'ignored_channels': ['c1']})
    settings = store.resolve()
    assert is_channel_ignored('alpha', 'c1', settings) is True


def test_evaluate_reply_trigger_blocks_ignored_channel():
    store = SettingsStore()
    store.global_overlay.channel.update({
        'reply_mode': 'all',
        'ignored_channels': ['alpha:c1'],
    })
    settings = store.resolve()
    result = evaluate_reply_trigger(_obs(mentioned=True), settings)
    assert result['allowed'] is False
    assert result['reason'] == 'channel_ignored'
