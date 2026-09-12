import random

from plugins.discord.conversation.reaction_service import ReactionService
from plugins.discord.conversation.sentiment import pick_reaction_emoji, sentiment_tier
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import EffectiveSettings, ReactionSettings


def make_obs(**kwargs):
    defaults = dict(
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
        content='this is amazing!',
        clean_content='this is amazing!',
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        attachments=[],
    )
    defaults.update(kwargs)
    return TextMessageObservation(**defaults)


def test_sentiment_tier_positive_without_vader(monkeypatch):
    monkeypatch.setattr('plugins.discord.conversation.sentiment._get_vader', lambda: False)
    assert sentiment_tier('this is amazing!') == 'positive'
    assert sentiment_tier('what do you think?') == 'curious'


def test_pick_reaction_emoji_returns_unicode():
    emoji = pick_reaction_emoji('I love this so much!!!', channel_name='general')
    assert emoji


def test_evaluate_silent_respects_chance(monkeypatch):
    monkeypatch.setattr(random, 'random', lambda: 0.0)
    service = ReactionService()
    settings = EffectiveSettings(reaction=ReactionSettings(enabled=True, silent_enabled=True, reaction_chance=100))
    obs = make_obs()
    intention = service.evaluate_silent(obs, settings=settings, world_state={'respond_trigger': False})
    assert intention is not None
    assert intention.emoji
    assert intention.reason == 'silent_sentiment'


def test_evaluate_silent_read_only_requires_organic_message():
    service = ReactionService()
    settings = EffectiveSettings(reaction=ReactionSettings(enabled=True, silent_enabled=True, read_only_enabled=True))
    obs = make_obs(mentioned=True)
    intention = service.evaluate_silent(
        obs,
        settings=settings,
        world_state={'respond_trigger': True},
        read_only=True,
    )
    assert intention is None


def test_execute_silent_records_dedupe(monkeypatch):
    monkeypatch.setattr('plugins.discord.conversation.reaction_service.time.sleep', lambda *_: None)

    class FakeTransport:
        def add_reaction_sync(self, channel_id, message_id, emoji, account_name=None):
            return {'status': 'reacted'}

    service = ReactionService()
    from plugins.discord.models.intentions import AddReactionIntention

    intention = AddReactionIntention(
        intention_type='add_reaction',
        account_name='alpha',
        channel_id='c1',
        message_id='m1',
        reason='silent_sentiment',
        emoji='👍',
    )
    result = service.execute_silent(intention, transport=FakeTransport())
    assert result['status'] == 'reacted'
    assert service._already_reacted(make_obs())


def test_execute_silent_schedules_on_daemon_loop(monkeypatch):
    import asyncio

    monkeypatch.setattr('plugins.discord.conversation.reaction_service.random.uniform', lambda *_: 0.0)

    class FakeTransport:
        loop = None

        async def add_reaction_async(self, channel_id, message_id, emoji, account_name=None):
            return {'status': 'reacted'}

    service = ReactionService()
    from plugins.discord.models.intentions import AddReactionIntention

    intention = AddReactionIntention(
        intention_type='add_reaction',
        account_name='alpha',
        channel_id='c1',
        message_id='m1',
        reason='silent_sentiment',
        emoji='👍',
    )

    async def _run():
        transport = FakeTransport()
        transport.loop = asyncio.get_running_loop()
        result = service.execute_silent(intention, transport=transport)
        assert result['status'] == 'scheduled'
        await asyncio.sleep(0.01)
        assert service._already_reacted(make_obs())

    asyncio.run(_run())
