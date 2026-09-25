"""Typed settings (S7): defaults, flat or nested overrides, coercion, retired sections ignored."""
from plugins.discord.models.settings import EffectiveSettings, MediaSettings, SettingsStore, apply_settings, nested


def test_defaults_are_the_manifest_story():
    s = SettingsStore().resolve()
    assert s.safety.allow_direct_messages is False and s.safety.dm_daily_budget == 30
    assert s.channel.reply_mode == 'default' and s.channel.batching_seconds == 8
    assert s.channel.context_messages == 20
    assert s.media.images_in_enabled is False and s.channel.natural_delay is True and s.bot.allow_all is False
    assert s.voice.addressing_mode == 'bot_name' and s.reaction.silent_enabled is True
    assert set(s.to_dict()) == {'safety', 'media', 'voice', 'bot', 'reaction', 'channel', 'presence', 'reminders'}
    assert s.reminders.enabled is False
    assert s.presence.enabled is False and s.presence.cycle_minutes == 30 and s.presence.away_line == 'away'


def test_overrides_flat_or_nested():
    assert SettingsStore({'channel.reply_mode': 'all'}).resolve().channel.reply_mode == 'all'
    assert SettingsStore({'channel': {'reply_mode': 'mentions_only'}}).resolve().channel.reply_mode == 'mentions_only'
    assert nested({'a.b': 1, 'c': {'d': 2}, 'bare': 3}) == {'a': {'b': 1}, 'c': {'d': 2}}


def test_values_are_coerced_to_field_types():
    s = SettingsStore({
        'safety': {'rate_limit_seconds': '45', 'allow_direct_messages': 'false'},
        'channel': {'context_messages': '7', 'ignored_channels': '["a", "b"]'},
        'voice': {'addressing_aliases': 'sapph, saphire', 'follow_up_seconds': 'nope'},
    }).resolve()
    assert s.safety.rate_limit_seconds == 45 and s.safety.allow_direct_messages is False
    assert s.channel.context_messages == 7
    assert s.voice.addressing_aliases == ['sapph', 'saphire']
    assert s.voice.follow_up_seconds == 20.0                    # unparseable → default stands
    assert s.channel.ignored_channels == ['a', 'b']


def test_retired_sections_and_keys_are_ignored_not_fatal():
    s = SettingsStore({'proactive': {'greeting_enabled': True}, 'retention': {'trace_days': 3, 'enabled': True},
                       'channel': {'no_such_key': 1, 'reply_mode': 'all'}, 'nonsense': 5}).resolve()
    assert s.channel.reply_mode == 'all' and not hasattr(s.channel, 'no_such_key')
    assert not hasattr(s, 'retention') and not hasattr(s, 'proactive')


def test_apply_settings_and_dataclass_construction():
    effective = EffectiveSettings(media=MediaSettings(gif_enabled=True, gif_api_key='abc'))
    apply_settings(effective, {'media': {'gif_provider': 'tenor'}})
    assert effective.media.gif_enabled and effective.media.gif_provider == 'tenor'
