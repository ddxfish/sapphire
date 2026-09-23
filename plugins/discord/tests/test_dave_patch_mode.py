import types
import sys

from plugins.discord.voice import dave_voice_patches as patches


def test_upstream_detection_is_version_based(monkeypatch):
    # M23 (hunt 2026-09-12): the gate reads py-cord's version, not the source
    # text of a private method (a reformat upstream flipped the old check).
    fake = types.SimpleNamespace(__version__='2.8.0rc2.dev22+g6e71bfffb')
    monkeypatch.setitem(sys.modules, 'discord', fake)
    assert patches._upstream_dave_decrypt_fixed() is True
    fake.__version__ = '2.6.1'
    assert patches._upstream_dave_decrypt_fixed() is False
    assert not hasattr(patches, 'inspect')


def test_requested_dave_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv('DISCORD_VOICE_DAVE_MODE', raising=False)
    assert patches.requested_dave_mode() == 'auto'


def test_requested_dave_mode_legacy_is_retired(monkeypatch):
    monkeypatch.setenv('DISCORD_VOICE_DAVE_MODE', 'legacy')
    assert patches.requested_dave_mode() == 'auto'          # the legacy decrypt branch is gone


def test_is_valid_opus_packet_rejects_random_f8_prefix():
    assert patches.is_valid_opus_packet(b'\xf8' + b'\xab\xcd' * 20) is False
    assert patches.is_valid_opus_packet(patches.OPUS_SILENCE) is True
