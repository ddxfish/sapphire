"""Voice receive dependency checks (py-cord sinks, not rapptz discord.py)."""

from __future__ import annotations

INSTALL_HINT = """Discord voice receive needs py-cord with DAVE support (not discord.py).
In the Sapphire venv run:
  pip uninstall discord.py -y
  pip install "py-cord[voice] @ git+https://github.com/Pycord-Development/pycord.git@6e71bfffb520f77948ff28544949925a55767ee7"
  pip install "davey>=0.1.4"
Then restart Sapphire.
Track: https://github.com/Pycord-Development/pycord/issues/3139"""


def voice_receive_available() -> bool:
    try:
        from discord.sinks import Sink
    except ImportError:
        return False
    return True


def dave_receive_available() -> bool:
    try:
        from discord.voice.utils.dependencies import HAS_DAVEY

        return bool(HAS_DAVEY)
    except ImportError:
        return False


def voice_receive_error() -> str:
    if not voice_receive_available():
        return INSTALL_HINT
    if not dave_receive_available():
        return (
            'Discord DAVE voice decryption needs the davey package.\n'
            '  pip install "davey>=0.1.4"\n'
            'Then restart Sapphire.'
        )
    return ''


def voice_stack_info() -> dict:
    """Runtime dependency snapshot for logs and diagnostics."""
    info = {
        'voice_sinks': voice_receive_available(),
        'davey': dave_receive_available(),
        'pycord_version': '',
        'dave_patches': False,
        'router_patches': False,
    }
    try:
        import discord

        info['pycord_version'] = str(getattr(discord, '__version__', '') or '')
    except ImportError:
        pass
    try:
        from plugins.discord.voice.dave_voice_patches import patch_mode

        info['dave_patch_mode'] = patch_mode()
    except ImportError:
        info['dave_patch_mode'] = 'none'
    try:
        from discord.voice.receive.reader import PacketDecryptor

        info['dave_patches'] = bool(
            getattr(PacketDecryptor.decrypt_rtp, '_discord_cognitive_dave_patched', False)
        )
    except ImportError:
        pass
    try:
        from discord.voice.receive.router import PacketRouter

        info['router_patches'] = bool(
            getattr(PacketRouter.run, '_discord_cognitive_patched', False)
        )
    except ImportError:
        pass
    try:
        from discord.opus import PacketDecoder

        info['opus_pcm_patch'] = bool(
            getattr(PacketDecoder._decode_packet, '_discord_cognitive_skip_pcm_dave', False)
        )
    except ImportError:
        info['opus_pcm_patch'] = False
    return info
