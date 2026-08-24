import types
import sys

from plugins.discord.voice import dave_voice_patches as patches


def test_upstream_detection_matches_fixed_reader(monkeypatch):
    def decrypt_rtp(self, packet):
        pass

    fake_reader = types.SimpleNamespace(PacketDecryptor=types.SimpleNamespace(decrypt_rtp=decrypt_rtp))
    monkeypatch.setitem(sys.modules, 'discord.voice.receive.reader', fake_reader)

    def fake_getsource(_obj):
        return (
            'def decrypt_rtp(self, packet):\n'
            ' dave.decrypt(uid, davey.MediaType.audio, dave_input)\n'
            ' if "UnencryptedWhenPassthroughDisabled" in str(exc):\n'
            '  pass\n'
        )

    monkeypatch.setattr(patches.inspect, 'getsource', fake_getsource)
    assert patches._upstream_dave_decrypt_fixed() is True


def test_upstream_detection_false_for_legacy_reader(monkeypatch):
    monkeypatch.setattr(patches.inspect, 'getsource', lambda _obj: 'def decrypt_rtp(self, packet): pass')
    assert patches._upstream_dave_decrypt_fixed() is False


def test_requested_dave_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv('DISCORD_VOICE_DAVE_MODE', raising=False)
    assert patches.requested_dave_mode() == 'auto'


def test_requested_dave_mode_legacy(monkeypatch):
    monkeypatch.setenv('DISCORD_VOICE_DAVE_MODE', 'legacy')
    assert patches.requested_dave_mode() == 'legacy'


def test_is_valid_opus_packet_rejects_random_f8_prefix():
    assert patches.is_valid_opus_packet(b'\xf8' + b'\xab\xcd' * 20) is False
    assert patches.is_valid_opus_packet(patches.OPUS_SILENCE) is True
