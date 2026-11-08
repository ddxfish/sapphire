"""A plugin-provided TTS/STT choice survives every unload that comes back.

2026-11-08: unload_plugin() persisted TTS_PROVIDER='none' on EVERY unload, so a
reload (button, env build, dev watcher, update, revert) silently turned her
voice off, and nothing rebound the live provider to the reloaded class. Now
only forget=True (disable, uninstall, unsigned policy, folder gone) erases the
choice; everything else detaches the live instance and
_reactivate_plugin_providers binds the new class.

No real settings are touched: settings.set, the registry and the system are fakes.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.plugin_loader import PluginLoader


class OldVoice:
    pass


class NewVoice:
    pass


def _registry(active_key, setting_key="TTS_PROVIDER", entries=None):
    reg = MagicMock()
    reg.setting_key = setting_key
    reg.get_active_key.return_value = active_key
    reg.get_entry.side_effect = lambda k: (entries or {}).get(k)
    return reg


def _loader_with(system_name, prov_key):
    loader = PluginLoader()
    loader._plugins["voiceplug"] = {
        "loaded": True, "enabled": True, "path": Path("/nonexistent"),
        "manifest": {}, "registered_providers": [(system_name, prov_key)],
    }
    return loader


@pytest.fixture
def settings_set():
    with patch("core.settings_manager.settings.set") as m:
        yield m


@pytest.mark.parametrize("system_name,switch", [("tts", "switch_tts_provider"),
                                                ("stt", "switch_stt_provider")])
def test_transient_unload_keeps_choice_and_detaches(settings_set, system_name, switch):
    loader = _loader_with(system_name, "voice8")
    reg = _registry("voice8", setting_key=f"{system_name.upper()}_PROVIDER")
    system = MagicMock()
    with patch.object(loader, "_get_provider_registry", return_value=reg), \
         patch.object(PluginLoader, "_live_system", return_value=system):
        loader.unload_plugin("voiceplug")
    settings_set.assert_not_called()                 # the saved choice is untouched
    getattr(system, switch).assert_called_once_with("none")
    reg.unregister_plugin.assert_called_once_with("voiceplug")


def test_forget_unload_erases_choice(settings_set):
    loader = _loader_with("tts", "voice8")
    reg = _registry("voice8")
    system = MagicMock()
    with patch.object(loader, "_get_provider_registry", return_value=reg), \
         patch.object(PluginLoader, "_live_system", return_value=system):
        loader.unload_plugin("voiceplug", forget=True)
    settings_set.assert_called_once_with("TTS_PROVIDER", "none", persist=True)
    system.switch_tts_provider.assert_not_called()


def test_inactive_provider_unload_touches_nothing(settings_set):
    loader = _loader_with("tts", "voice8")
    reg = _registry("kokoro")
    system = MagicMock()
    with patch.object(loader, "_get_provider_registry", return_value=reg), \
         patch.object(PluginLoader, "_live_system", return_value=system):
        loader.unload_plugin("voiceplug")
    settings_set.assert_not_called()
    system.switch_tts_provider.assert_not_called()


def test_embedding_keeps_persisted_reset(settings_set):
    """No live hot-swap leg for embedding (re-embed interlocks): unchanged behaviour."""
    loader = _loader_with("embedding", "emb8")
    reg = _registry("emb8", setting_key="EMBEDDING_PROVIDER")
    with patch.object(loader, "_get_provider_registry", return_value=reg), \
         patch.object(PluginLoader, "_live_system", return_value=MagicMock()):
        loader.unload_plugin("voiceplug")
    settings_set.assert_called_once_with("EMBEDDING_PROVIDER", "none", persist=True)


def test_failed_load_at_boot_keeps_choice(settings_set):
    """Boot cleanup (no system yet) must not erase the choice: silent until fixed."""
    loader = _loader_with("tts", "voice8")
    reg = _registry("voice8")
    with patch.object(loader, "_get_provider_registry", return_value=reg), \
         patch.object(PluginLoader, "_live_system", return_value=None):
        loader.unload_plugin("voiceplug")
    settings_set.assert_not_called()


def _system_with_tts(instance):
    return SimpleNamespace(tts=SimpleNamespace(provider=instance),
                           whisper_client=None,
                           switch_tts_provider=MagicMock(),
                           switch_stt_provider=MagicMock())


def _reactivate(system, entries, tts_key="voice8"):
    loader = PluginLoader()
    regs = {"tts": _registry(tts_key, entries=entries), "stt": _registry("none", "STT_PROVIDER")}
    with patch.object(loader, "_get_provider_registry", side_effect=lambda n: regs.get(n)), \
         patch.object(PluginLoader, "_live_system", return_value=system), \
         patch("config.TTS_PROVIDER", tts_key, create=True), \
         patch("config.STT_PROVIDER", "none", create=True), \
         patch("core.event_bus.publish") as publish:
        loader._reactivate_plugin_providers()
    return publish


def test_rebind_stale_class_after_reload():
    system = _system_with_tts(OldVoice())
    publish = _reactivate(system, {"voice8": {"class": NewVoice, "plugin_name": "voiceplug"}})
    system.switch_tts_provider.assert_called_once_with("voice8")
    assert publish.call_args[0][1] == {"key": "TTS_PROVIDER", "value": "voice8"}


def test_rebind_from_detached_null():
    system = _system_with_tts(None)
    _reactivate(system, {"voice8": {"class": NewVoice, "plugin_name": "voiceplug"}})
    system.switch_tts_provider.assert_called_once_with("voice8")


def test_no_rebind_when_class_matches():
    system = _system_with_tts(NewVoice())
    _reactivate(system, {"voice8": {"class": NewVoice, "plugin_name": "voiceplug"}})
    system.switch_tts_provider.assert_not_called()


def test_no_rebind_when_plugin_not_loaded():
    """Failed load: the key names nothing registered, so she stays silent."""
    system = _system_with_tts(None)
    _reactivate(system, {})
    system.switch_tts_provider.assert_not_called()


def test_core_provider_never_rebound():
    system = _system_with_tts(OldVoice())
    _reactivate(system, {"kokoro": {"class": NewVoice}}, tts_key="kokoro")
    system.switch_tts_provider.assert_not_called()


def test_plugins_ready_rebinds():
    loader = PluginLoader()
    with patch.object(loader, "_reactivate_plugin_providers") as react:
        loader._fire_plugins_ready("reload")
    react.assert_called_once()


def test_reload_unloads_without_forget():
    loader = PluginLoader()
    loader._plugins["voiceplug"] = {"enabled": False, "path": Path("/nonexistent")}
    with patch.object(loader, "unload_plugin") as unload, \
         patch.object(loader, "refresh_from_disk"):
        loader.reload_plugin("voiceplug")
    unload.assert_called_once_with("voiceplug")
