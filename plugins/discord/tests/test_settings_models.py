from plugins.discord.models.settings import MediaSettings, SettingsOverlay, SettingsStore


def test_settings_defaults_and_merge_behavior():
    store = SettingsStore()
    merged = store.resolve()
    assert merged.cognitive.llm_primary == "auto"
    assert merged.safety.allow_direct_messages is False   # DMs opt-in since 2026-09-13

    store.global_overlay = SettingsOverlay.from_dict({
        "cognitive": {"llm_primary": "ollama", "llm_model": "llama3.2"},
        "safety": {"allow_direct_messages": True},
    })
    store.guild_overrides["guild-1"] = SettingsOverlay.from_dict({
        "cognitive": {"llm_model": "guild-model"}
    })
    store.channel_overrides["channel-1"] = SettingsOverlay.from_dict({
        "cognitive": {"llm_primary": "claude"}
    })

    resolved = store.resolve(guild_id="guild-1", channel_id="channel-1")
    assert resolved.cognitive.llm_primary == "claude"
    assert resolved.cognitive.llm_model == "guild-model"
    assert resolved.safety.allow_direct_messages is True


def test_retired_sections_are_ignored_not_fatal():
    # Stored settings from 1.x still carry proactive./presence./profile. keys (S1 + S2, 2026-09-22).
    overlay = SettingsOverlay.from_dict({"proactive": {"greeting_enabled": True}, "presence": {"status": "idle"},
                                         "profile": {"birthday_capture_enabled": True, "enabled": False}})
    store = SettingsStore(global_overlay=overlay)
    resolved = store.resolve()
    assert not hasattr(resolved, "proactive") and not hasattr(resolved, "presence")
    assert not hasattr(resolved, "profile")


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
    assert settings.vision_provider == 'auto'
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
