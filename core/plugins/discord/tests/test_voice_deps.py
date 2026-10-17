from plugins.discord.voice.voice_deps import INSTALL_HINT, voice_receive_available


def test_voice_receive_hint_when_sinks_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'discord.sinks' or name.startswith('discord.sinks.'):
            raise ImportError('no sinks')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    assert voice_receive_available() is False
    assert 'py-cord' in INSTALL_HINT
