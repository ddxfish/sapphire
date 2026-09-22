from plugins.discord.models.settings import DebugSettings, MediaSettings, SettingsOverlay, SettingsStore, overlay_from_flat


def test_settings_defaults_and_global_layer():
    # Per-guild / channel / DM overlays are gone (S6): one global layer, read live from core.
    store = SettingsStore.from_dict({
        "global": {"channel": {"reply_mode": "mentions_only"}, "safety": {"allow_direct_messages": True}},
        "guilds": {"guild-1": {"channel": {"reply_mode": "all"}}},
    })
    resolved = store.resolve()
    assert resolved.channel.reply_mode == "mentions_only" and resolved.safety.allow_direct_messages is True
    assert store.to_dict() == {"global": {"channel": {"reply_mode": "mentions_only"}, "safety": {"allow_direct_messages": True}}}
    assert not hasattr(store, "guild_overrides")


def test_retired_sections_are_ignored_not_fatal():
    # Stored settings from 1.x still carry proactive./presence./profile./cognitive./delivery. keys (S1-S5, 2026-09-22).
    overlay = SettingsOverlay.from_dict({"proactive": {"greeting_enabled": True}, "presence": {"status": "idle"},
                                         "profile": {"enabled": False}, "cognitive": {"llm_primary": "claude"},
                                         "delivery": {"auto_typo_enabled": True},
                                         "media": {"vision_llm_provider": "x", "images_in_enabled": True}})
    resolved = SettingsStore(global_overlay=overlay).resolve()
    for gone in ("proactive", "presence", "profile", "cognitive", "delivery"):
        assert not hasattr(resolved, gone), gone
    assert resolved.media.images_in_enabled is True and not hasattr(resolved.media, "vision_llm_provider")


def test_overlay_round_trip():
    overlay = SettingsOverlay.from_dict({
        "dm": {"reply_mode": "mentions_only"},
        "voice": {"enabled": True, "transcription_enabled": True},
    })
    assert overlay.to_dict() == {
        "dm": {"reply_mode": "mentions_only"},
        "voice": {"enabled": True, "transcription_enabled": True},
    }


def test_media_settings_exposes_image_defaults():
    settings = MediaSettings()
    assert settings.images_in_enabled is False and settings.max_images == 4
    assert settings.gif_enabled is False and settings.gif_provider == "klipy"


def test_debug_settings_default_off():
    assert DebugSettings().llm_debug_enabled is False


def test_overlay_from_flat_maps_dotted_keys():
    overlay = overlay_from_flat({
        'channel.reply_mode': 'all',
        'media.gif_enabled': True,
        'not_a_dotted_key': 'ignored',
        'unknown_section.field': 'dropped',
    })
    assert overlay.channel['reply_mode'] == 'all'
    assert overlay.media['gif_enabled'] is True
    assert isinstance(overlay, SettingsOverlay)
