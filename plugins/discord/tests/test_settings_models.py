from plugins.discord.models.settings import MediaSettings, SettingsOverlay, SettingsStore


def test_settings_defaults_and_merge_behavior():
    store = SettingsStore()
    merged = store.resolve()
    assert merged.presence.status == "online"
    assert merged.safety.allow_direct_messages is True

    store.global_overlay = SettingsOverlay.from_dict({
        "presence": {"status": "idle", "activity": "thinking"},
        "safety": {"allow_direct_messages": False},
    })
    store.guild_overrides["guild-1"] = SettingsOverlay.from_dict({
        "presence": {"activity": "guild activity"}
    })
    store.channel_overrides["channel-1"] = SettingsOverlay.from_dict({
        "presence": {"status": "dnd"}
    })

    resolved = store.resolve(guild_id="guild-1", channel_id="channel-1")
    assert resolved.presence.status == "dnd"
    assert resolved.presence.activity == "guild activity"
    assert resolved.safety.allow_direct_messages is False


def test_overlay_round_trip():
    overlay = SettingsOverlay.from_dict({
        "dm": {"reply_mode": "mentions_only"},
        "voice": {"enabled": True, "transcription_enabled": True},
    })
    payload = overlay.to_dict()
    assert payload["dm"]["reply_mode"] == "mentions_only"
    assert payload["voice"]["enabled"] is True


def test_media_settings_exposes_vision_defaults():
    settings = MediaSettings()

    assert settings.image_understanding_enabled is False
    assert settings.vision_provider == 'openai_compat'
    assert settings.vision_base_url == ''
    assert settings.vision_model == ''
    assert settings.vision_api_key == ''
    assert settings.vision_timeout_seconds == 30
    assert settings.vision_gif_mode == 'first_frame'
    assert settings.vision_debug_enabled is False


def test_cognitive_settings_exposes_llm_defaults():
    from plugins.discord.models.settings import CognitiveSettings

    settings = CognitiveSettings()
    assert settings.llm_primary == 'auto'
    assert settings.llm_model == ''
