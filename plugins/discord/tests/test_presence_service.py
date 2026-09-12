from plugins.discord.models.profiles import AgentAffect
from plugins.discord.models.settings import EffectiveSettings, PresenceSettings, ProactiveSettings
from plugins.discord.presence.presence_catalog import load_awake_presets, load_sleep_statuses
from plugins.discord.transport.discord_presence import DiscordPresenceService


def test_leona_awake_catalog_loaded():
    presets = load_awake_presets()
    assert len(presets) >= 50
    assert any(p['id'] == 'listening_chat' for p in presets)


def test_leona_sleep_catalog_loaded():
    statuses = load_sleep_statuses()
    assert len(statuses) >= 50
    assert 'custom: sleeping' in statuses


def test_awake_presence_during_day():
    service = DiscordPresenceService()
    settings = EffectiveSettings(
        presence=PresenceSettings(status='online', activity='Chatting'),
        proactive=ProactiveSettings(sleep_schedule_enabled=True, sleep_utc_hour=22, greeting_utc_hour=9),
    )
    affect = AgentAffect(energy=0.8, sociability=0.7)

    choice = service.select_presence(settings, affect, asleep=False, forced_wake=False, local_hour=12)

    assert choice['status'] == 'online'
    assert choice['activity'] == 'Chatting'


def test_sleep_presence_when_asleep():
    service = DiscordPresenceService()
    settings = EffectiveSettings(
        presence=PresenceSettings(status='online', quiet_status='idle', sleep_activity='custom: sleeping'),
        proactive=ProactiveSettings(sleep_schedule_enabled=True),
    )

    choice = service.select_presence(settings, AgentAffect(), asleep=True, forced_wake=False, local_hour=23)

    assert choice['mode'] == 'sleep'
    assert choice['status'] == 'idle'
    assert choice['activity']


def test_should_update_respects_interval():
    service = DiscordPresenceService()
    service.mark_updated('bot', 'awake')
    assert service.should_update('bot', mode='awake', interval_seconds=300) is False
    assert service.should_update('bot', mode='sleep', interval_seconds=300) is True


def test_cycling_picks_from_pool():
    service = DiscordPresenceService()
    settings = EffectiveSettings(
        presence=PresenceSettings(
            status='online',
            cycling_enabled=True,
            activity_presets=['clear', 'listening_chat'],
        ),
    )
    choice = service.select_presence(settings, AgentAffect(), asleep=False, forced_wake=False, local_hour=12)
    assert choice['mode'] == 'awake'
    assert choice['activity'] in {'', 'listening: chat'}
