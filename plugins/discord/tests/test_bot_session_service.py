import time

from plugins.discord.conversation.bot_session_service import BotSessionService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import BotInteractionSettings, EffectiveSettings


SAPPHIRE_ID = 'sapphire-bot-1'
SPEEDYBOI_ID = 'speedyboi-99'
REMMi_ID = 'remmi-self'


def _settings(**kwargs):
    defaults = dict(
        enabled=True,
        reply_mode='allowlist',
        allowlist_ids=[SAPPHIRE_ID],
        session_human_window_seconds=300,
        session_silence_seconds=150,
    )
    defaults.update(kwargs)
    bot = BotInteractionSettings(**defaults)
    return EffectiveSettings(bot=bot)


def _obs(**kwargs):
    base = dict(
        observation_id='obs-1',
        account_name='remmi',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id=SAPPHIRE_ID,
        username='Sapphire',
        display_name='Sapphire',
        message_id='m1',
        content='hello Remmi',
        clean_content='hello Remmi',
        created_at=time.time(),
        is_dm=False,
        mentioned=False,
        author_is_bot=True,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def test_speedyboi_not_allowlisted():
    service = BotSessionService()
    decision = service.evaluate(
        _obs(author_id=SPEEDYBOI_ID, username='speedyboi', content='Download: 900Mbps'),
        _settings(),
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is False
    assert decision['reason'] == 'bot_not_allowlisted'


def test_sapphire_direct_mention_allowed():
    service = BotSessionService()
    decision = service.evaluate(
        _obs(mentioned=True, content='<@remmi> your turn'),
        _settings(),
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is True


def test_human_tags_remii_then_sapphire_reply_chain():
    service = BotSessionService()
    settings = _settings()
    human = _obs(
        author_id='human-1',
        username='zeebie',
        author_is_bot=False,
        mentioned=True,
        content='@Remmi @Sapphire debate pizza',
        message_id='h1',
    )
    service.evaluate(
        human,
        settings,
        respond_trigger=True,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    service.record_sent_message('remmi', 'c1', 'remmi-msg-1')
    decision = service.evaluate(
        _obs(
            message_id='s1',
            reply_to_message_id='remmi-msg-1',
            content='pineapple belongs on pizza',
        ),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is True


def test_sapphire_side_comment_blocked_during_session():
    service = BotSessionService()
    settings = _settings()
    service.evaluate(
        _obs(author_id='human-1', author_is_bot=False, mentioned=True, message_id='h1'),
        settings,
        respond_trigger=True,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    service.record_sent_message('remmi', 'c1', 'remmi-msg-1')
    service.evaluate(
        _obs(message_id='s1', reply_to_message_id='remmi-msg-1'),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    decision = service.evaluate(
        _obs(message_id='s2', content='unrelated side note', clean_content='unrelated side note'),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is False


def test_session_expires_after_silence(monkeypatch):
    service = BotSessionService()
    settings = _settings(session_silence_seconds=60)
    t0 = 1_000_000.0
    monkeypatch.setattr(time, 'time', lambda: t0)
    service.evaluate(
        _obs(mentioned=True),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    monkeypatch.setattr(time, 'time', lambda: t0 + 120)
    decision = service.evaluate(
        _obs(message_id='s2', content='still going?', clean_content='still going?', mentioned=False),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is True
    assert decision['reason'] == 'bot_organic_candidate'
    assert decision.get('organic_candidate') is True


def test_idle_allowlisted_bot_is_organic_candidate():
    service = BotSessionService()
    decision = service.evaluate(
        _obs(content='random bot chatter', clean_content='random bot chatter', mentioned=False),
        _settings(),
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is True
    assert decision['reason'] == 'bot_organic_candidate'
    assert decision.get('organic_candidate') is True


def test_safety_cap_blocks_long_chains():
    service = BotSessionService()
    settings = _settings(session_safety_max_exchanges=3)
    for index in range(3):
        decision = service.evaluate(
            _obs(message_id=f's{index}', mentioned=True),
            settings,
            respond_trigger=False,
            bot_names={'Remmi'},
            name_match_enabled=True,
        )
        assert decision['allowed'] is True
    decision = service.evaluate(
        _obs(message_id='s-final', mentioned=True),
        settings,
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is False
    assert decision['reason'] == 'bot_session_safety_cap'


def test_safety_cap_resets_when_human_reengages():
    # The cap is per-debate, not a lifetime channel budget: a human
    # re-addressing her zeroes the exchange counter.
    service = BotSessionService()
    settings = _settings(session_safety_max_exchanges=2)

    def _bot_mention(mid):
        return _obs(mentioned=True, message_id=mid, content='<@remmi> go')

    assert service.evaluate(_bot_mention('m1'), settings, respond_trigger=False,
                            bot_names={'Remmi'}, name_match_enabled=True)['allowed'] is True
    assert service.evaluate(_bot_mention('m2'), settings, respond_trigger=False,
                            bot_names={'Remmi'}, name_match_enabled=True)['allowed'] is True
    capped = service.evaluate(_bot_mention('m3'), settings, respond_trigger=False,
                              bot_names={'Remmi'}, name_match_enabled=True)
    assert capped['allowed'] is False
    assert capped['reason'] == 'bot_session_safety_cap'

    human = _obs(author_id='human-1', author_is_bot=False, mentioned=True,
                 message_id='h1', content='Remmi keep going')
    service.evaluate(human, settings, respond_trigger=True,
                     bot_names={'Remmi'}, name_match_enabled=True)
    fresh = service.evaluate(_bot_mention('m4'), settings, respond_trigger=False,
                             bot_names={'Remmi'}, name_match_enabled=True)
    assert fresh['allowed'] is True


def test_reply_chain_outside_window_is_organic_candidate():
    # Reply-to-our-message alone no longer opens a debate session; it may
    # still qualify for an organic chance roll when the bot is allowlisted.
    service = BotSessionService()
    service.record_sent_message('remmi', 'c1', 'ours-1')
    decision = service.evaluate(
        _obs(reply_to_message_id='ours-1', content='and another thing',
             clean_content='and another thing'),
        _settings(),
        respond_trigger=False,
        bot_names={'Remmi'},
        name_match_enabled=True,
    )
    assert decision['allowed'] is True
    assert decision.get('organic_candidate') is True
    assert decision['reason'] == 'bot_organic_candidate'
