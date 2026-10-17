import pytest


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("discord.opus") is None,
    reason="py-cord not installed",
)
def test_opus_pcm_dave_double_decrypt_patch_applied():
    from discord.opus import PacketDecoder

    from plugins.discord.voice.pycord_patches import apply_pycord_voice_patches

    apply_pycord_voice_patches()

    assert getattr(PacketDecoder._decode_packet, "_discord_host_skip_pcm_dave", False) is True

    import inspect

    source = inspect.getsource(PacketDecoder._decode_packet)
    assert "dave.decrypt" not in source


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("discord.opus") is None,
    reason="py-cord not installed",
)
def test_decode_opus_payload_accepts_dave_opus_toc():
    from discord.opus import Decoder

    from plugins.discord.voice.dave_voice_patches import looks_like_opus_payload
    from plugins.discord.voice.pycord_patches import _decode_opus_payload

    packet = bytes.fromhex("789f9780") + b"\x00" * 141
    assert looks_like_opus_payload(packet) is False
    decoder, pcm = _decode_opus_payload(Decoder(), packet, fec=False)
    assert len(pcm) > 0


_REKEY_SUBPROCESS_SCRIPT = r"""
import asyncio
from discord.voice.gateway import VoiceWebSocket
from plugins.discord.voice.pycord_patches import apply_pycord_voice_patches

apply_pycord_voice_patches()
assert getattr(VoiceWebSocket.load_secret_key, '_discord_host_rekey', False)

class FakeReader:
    def __init__(self):
        self.keys = []
    def update_secret_key(self, key):
        self.keys.append(key)

class FakeClient:
    def __init__(self):
        self._reader = FakeReader()

class FakeState:
    def __init__(self):
        self.client = FakeClient()
        self.secret_key = None
        self.channel_id = 1
        self.guild_id = 1

ws = object.__new__(VoiceWebSocket)
ws.state = FakeState()
async def _speak(_state):
    return None
ws.speak = _speak

key = list(range(32))
asyncio.run(VoiceWebSocket.load_secret_key(ws, {'secret_key': key}))
assert ws.state.secret_key == key, ws.state.secret_key
assert ws.state.client._reader.keys == [bytes(range(32))], ws.state.client._reader.keys
print('REKEY_RESYNC_OK')
"""


def test_rekey_resync_pushes_key_into_reader():
    """Real-py-cord check in a subprocess — inside pytest, sys.modules['discord']
    is THIS plugin (pytest basedir walk stops at user/plugins/, importing tests
    as discord.tests.*), so py-cord is unreachable in-process and the other
    pycord tests above silently skip. A clean interpreter has no such shadow."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Walk up to the Sapphire root (has core/) — works at either band depth
    # (plugins/discord/tests vs user/plugins/discord/tests).
    repo_root = Path(__file__).absolute().parent
    while not (repo_root / 'core').is_dir():
        if repo_root.parent == repo_root:
            raise RuntimeError('Sapphire root not found above this test')
        repo_root = repo_root.parent
    env = dict(os.environ)
    # user/ first so the namespace package 'plugins' finds THIS tree's
    # plugins.discord (the house copy has no voice/pycord_patches).
    env['PYTHONPATH'] = os.pathsep.join([str(repo_root / 'user'), str(repo_root)])
    result = subprocess.run(
        [sys.executable, '-c', _REKEY_SUBPROCESS_SCRIPT],
        capture_output=True, text=True, timeout=120,
        cwd=str(repo_root), env=env,
    )
    if 'No module named' in (result.stderr or '') and 'discord' in (result.stderr or ''):
        import pytest
        pytest.skip('py-cord not installed')
    assert result.returncode == 0, result.stderr
    assert 'REKEY_RESYNC_OK' in result.stdout


def test_aead_flood_filter_rate_limits():
    import logging

    from plugins.discord.voice.pycord_patches import AeadFloodFilter

    f = AeadFloodFilter()

    def rec(msg):
        return logging.LogRecord('discord.voice.receive.reader', logging.ERROR, 'reader.py', 1, msg, (), None)

    # First failure passes, the burst behind it is suppressed.
    assert f.filter(rec('Critical error at AEAD: %s')) is True
    assert f.filter(rec('Critical error at AEAD: %s')) is False
    assert f.filter(rec('CryptoError while decoding a voice packet')) is False
    assert f._suppressed == 2
    # Unrelated records always pass.
    assert f.filter(rec('Socket reader started')) is True
    # After the window, the next failure passes again.
    f._allowed_at = 0.0
    assert f.filter(rec('Critical error at AEAD: %s')) is True
    assert f._suppressed == 0
