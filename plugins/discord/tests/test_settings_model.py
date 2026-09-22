"""Typed settings (S7): defaults, flat or nested overrides, coercion, retired sections ignored."""
from plugins.discord.models.settings import EffectiveSettings, MediaSettings, SettingsStore, apply_settings, nested


def test_defaults_are_the_manifest_story():
    s = SettingsStore().resolve()
    assert s.safety.allow_direct_messages is False and s.safety.dm_daily_budget == 30
    assert s.channel.reply_mode == 'default' and s.channel.batching_seconds == 8
    assert s.retention.enabled is False and s.retention.message_days == 90
    assert s.media.images_in_enabled is False and s.debug.llm_debug_enabled is False
    assert s.voice.addressing_mode == 'bot_name' and s.reaction.silent_enabled is True
    assert set(s.to_dict()) == {'safety', 'media', 'voice', 'retention', 'debug', 'bot', 'reaction', 'channel'}


def test_overrides_flat_or_nested():
    assert SettingsStore({'channel.reply_mode': 'all'}).resolve().channel.reply_mode == 'all'
    assert SettingsStore({'channel': {'reply_mode': 'mentions_only'}}).resolve().channel.reply_mode == 'mentions_only'
    assert nested({'a.b': 1, 'c': {'d': 2}, 'bare': 3}) == {'a': {'b': 1}, 'c': {'d': 2}}


def test_values_are_coerced_to_field_types():
    s = SettingsStore({
        'safety': {'rate_limit_seconds': '45', 'allow_direct_messages': 'false'},
        'retention': {'message_days': '3'},
        'voice': {'addressing_aliases': 'sapph, saphire', 'follow_up_seconds': 'nope'},
        'channel': {'ignored_channels': '["a", "b"]'},
    }).resolve()
    assert s.safety.rate_limit_seconds == 45 and s.safety.allow_direct_messages is False
    assert s.retention.message_days == 3
    assert s.voice.addressing_aliases == ['sapph', 'saphire']
    assert s.voice.follow_up_seconds == 20.0                    # unparseable → default stands
    assert s.channel.ignored_channels == ['a', 'b']


def test_retired_sections_and_keys_are_ignored_not_fatal():
    s = SettingsStore({'proactive': {'greeting_enabled': True}, 'retention': {'trace_days': 3, 'enabled': True},
                       'nonsense': 5}).resolve()
    assert s.retention.enabled is True and not hasattr(s.retention, 'trace_days') and not hasattr(s, 'proactive')


def test_apply_settings_and_dataclass_construction():
    effective = EffectiveSettings(media=MediaSettings(gif_enabled=True, gif_api_key='abc'))
    apply_settings(effective, {'media': {'gif_provider': 'tenor'}})
    assert effective.media.gif_enabled and effective.media.gif_provider == 'tenor'
