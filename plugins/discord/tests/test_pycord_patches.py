import pytest


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("discord.opus") is None,
    reason="py-cord not installed",
)
def test_opus_pcm_dave_double_decrypt_patch_applied():
    from discord.opus import PacketDecoder

    from plugins.discord.voice.pycord_patches import apply_pycord_voice_patches

    apply_pycord_voice_patches()

    assert getattr(PacketDecoder._decode_packet, "_discord_cognitive_skip_pcm_dave", False) is True

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
