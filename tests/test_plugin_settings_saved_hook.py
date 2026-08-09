"""Generic plugin settings-saved hook + PLUGIN_NOTICE event (core, 2026-06-16).

These test CORE behavior (core/event_bus.py + core/routes/plugins.py), not any
one plugin — the PUT settings route notifies the active provider so a plugin can
react to its own settings (e.g. Piper's download-on-save), and the toast event
exists end-to-end. Piper's own provider logic is tested in
plugins/piper/tests/test_piper_provider.py.
"""
import json
from pathlib import Path


def test_plugin_notice_event_constant_exists():
    from core.event_bus import Events
    assert Events.PLUGIN_NOTICE == "plugin_notice"


def test_frontend_event_bus_mirrors_plugin_notice():
    js = (Path(__file__).resolve().parents[1] /
          "interfaces" / "web" / "static" / "core" / "event-bus.js").read_text()
    assert "plugin_notice" in js, "frontend Events mirror missing PLUGIN_NOTICE"


def test_settings_put_calls_provider_on_settings_saved(client, mock_system, monkeypatch, tmp_path):
    """PUT plugin settings notifies the active provider's optional hook."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)

    r = c.put("/api/webui/plugins/piper/settings",
              json={"settings": {"voice": "en_US-lessac-medium"}},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    prov = mock_system.tts.provider
    prov.on_settings_saved.assert_called()
    args = prov.on_settings_saved.call_args[0]
    assert args[0] == "piper"
    assert args[1].get("voice") == "en_US-lessac-medium"


def test_settings_put_isolates_provider_exception(client, mock_system, monkeypatch, tmp_path):
    """R1: a provider on_settings_saved blow-up must NOT lose the saved settings."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)
    mock_system.tts.provider.on_settings_saved.side_effect = RuntimeError("boom")

    r = c.put("/api/webui/plugins/piper/settings",
              json={"settings": {"voice": "en_US-amy-low"}},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    saved = json.loads((tmp_path / "piper.json").read_text())
    assert saved["voice"] == "en_US-amy-low", "settings must persist despite hook exception"


def test_settings_put_reaches_stt_provider(client, mock_system, monkeypatch, tmp_path):
    """#2 regression: whisper_client IS the STT provider (it has no `.provider`).
    The resolver must notify it directly — the old loop looked for
    `system.stt.provider` and silently missed it."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)

    r = c.put("/api/webui/plugins/piper/settings",
              json={"settings": {"voice": "en_US-amy-low"}},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    mock_system.whisper_client.on_settings_saved.assert_called()


def test_settings_put_passes_a_copy_not_the_live_dict(client, mock_system, monkeypatch, tmp_path):
    """#4 regression: a provider that mutates its settings arg must NOT leak into
    the response — the hook passes dict(merged), not the live dict."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)
    mock_system.tts.provider.on_settings_saved.side_effect = \
        lambda name, s: s.update({"INJECTED_BY_PROVIDER": True})

    r = c.put("/api/webui/plugins/piper/settings",
              json={"settings": {"voice": "en_US-lessac-low"}},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert "INJECTED_BY_PROVIDER" not in r.json().get("settings", {}), \
        "provider mutation leaked into the response — hook must pass a copy"


def test_plugin_notice_not_replayed_to_late_subscribers():
    """#6 regression: toasts are ephemeral — plugin_notice must NOT enter the
    replay buffer (a freshly-opened tab shouldn't surface a stale 'done' toast).
    Normal events still replay."""
    from core.event_bus import EventBus
    bus = EventBus()
    bus.publish("plugin_notice", {"message": "ephemeral"})
    bus.publish("plugin_reloaded", {"plugin": "x"})
    buffered = [e["type"] for e in bus._replay_buffer]
    assert "plugin_notice" not in buffered, "ephemeral toast must not be replayed"
    assert "plugin_reloaded" in buffered, "normal events must still replay"


def test_settings_delete_removes_file_and_propagates(client, mock_system, monkeypatch, tmp_path):
    """DELETE (reset) must remove the stored file AND run the same propagation
    as a save — otherwise settings-aware tool schemas / provider state keep
    serving pre-reset values until restart (UI reset button, 2026-08-09)."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)
    monkeypatch.setattr("core.plugin_loader.plugin_loader.get_plugin_settings",
                        lambda n: {"voice": "manifest-default"})
    (tmp_path / "piper.json").write_text(json.dumps({"voice": "customized"}))

    r = c.delete("/api/webui/plugins/piper/settings",
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert not (tmp_path / "piper.json").exists(), "stored settings file must be deleted"
    prov = mock_system.tts.provider
    prov.on_settings_saved.assert_called()
    args = prov.on_settings_saved.call_args[0]
    assert args[0] == "piper"
    assert args[1].get("voice") == "manifest-default", \
        "hook must receive post-reset effective settings (defaults), not the old stored ones"


def test_settings_delete_without_stored_file_is_ok(client, mock_system, monkeypatch, tmp_path):
    """Resetting a plugin that never stored settings is a clean no-op success."""
    c, csrf = client
    from core.routes import plugins as plug
    monkeypatch.setattr(plug, "_require_known_plugin", lambda n: None)
    monkeypatch.setattr(plug, "USER_PLUGIN_SETTINGS_DIR", tmp_path)

    r = c.delete("/api/webui/plugins/piper/settings",
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json().get("status") == "success"
