from plugins.discord.conversation.name_match import bot_names_for_account, message_matches_bot_name
from plugins.discord.conversation.trigger_service import evaluate_reply_trigger
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore


def _obs(**kwargs):
    base = dict(
        observation_id='obs-1',
        account_name='leona_bot_test',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id='m1',
        content='hey leona_bot_test are you there',
        clean_content='hey leona_bot_test are you there',
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


class FakeTransport:
    def account_health(self, name):
        return {'bot_name': 'LeonaBot'}


def test_message_matches_bot_name_insensitive():
    names = {'LeonaBot', 'leona_bot_test'}
    assert message_matches_bot_name('hey LeonaBot', names) is True
    assert message_matches_bot_name('hello world', names) is False


def test_evaluate_reply_trigger_name_match():
    store = SettingsStore()
    store.global_overlay.channel.update({'name_match_enabled': True})
    settings = store.resolve()
    obs = _obs(clean_content='hey LeonaBot whats up')

    result = evaluate_reply_trigger(obs, settings, transport=FakeTransport())

    assert result['name_matched'] is True
    assert result['respond_trigger'] is True
    assert result['allowed'] is True


def test_evaluate_reply_trigger_mentions_only_blocks_organic():
    store = SettingsStore()
    store.global_overlay.channel.update({'reply_mode': 'mentions_only', 'name_match_enabled': False})
    settings = store.resolve()
    obs = _obs(clean_content='hello everyone')

    result = evaluate_reply_trigger(obs, settings, transport=FakeTransport())

    assert result['allowed'] is False
    assert result['reason'] == 'mentions_only'
