from types import SimpleNamespace

from plugins.discord.voice.dave_voice_patches import recover_passthrough_opus


def test_recovers_opus_from_passthrough_trailer():
    packet = SimpleNamespace(padding=False, extended=True, _outer_decrypted=None)
    # fake opus byte + dave supplemental (size 12) ending in fafa
    opus = b'\xf8\xff\xfe' + b'\xab\xcd'
    supp_size = 12
    trailer = b'\x00' * (supp_size - 3) + bytes([supp_size]) + b'\xfa\xfa'
    payload = opus + trailer
    recovered = recover_passthrough_opus(packet, payload)
    assert recovered == opus


def test_recovers_opus_with_rtp_padding_before_trailer():
    packet = SimpleNamespace(padding=True, extended=False, _outer_decrypted=None)
    opus = b'\xfc\x03'
    supp_size = 8
    trailer = b'\x00' * (supp_size - 3) + bytes([supp_size]) + b'\xfa\xfa'
    # RFC 3550: pad count byte is the LAST octet and includes itself.
    pad_n = 3
    payload = opus + trailer + b'\x00' * (pad_n - 1) + bytes([pad_n])
    recovered = recover_passthrough_opus(packet, payload)
    assert recovered.startswith(opus)


def test_does_not_treat_ciphertext_as_opus_without_fafa_trailer():
    packet = SimpleNamespace(padding=False, extended=False, _outer_decrypted=None)
    ciphertext = b'\xf8' + b'\xab\xcd' * 20
    assert recover_passthrough_opus(packet, ciphertext) == b'\xf8\xff\xfe'


def test_encrypted_dave_frame_must_not_skip_dave_decrypt():
    """Encrypted DAVE frames also end in 0xFAFA — the trailer heuristic alone
    says "passthrough" for ciphertext, so the production recovery gate must
    reject it. (The old assertion leaned on `opus_decodable` being False, which
    only held while py-cord was shadowed under pytest — real libopus happily
    decodes ciphertext into noise.)"""
    from plugins.discord.voice.dave_voice_patches import (
        _recover_silence_passthrough,
        looks_like_passthrough_payload,
    )

    packet = SimpleNamespace(padding=False, extended=True, _outer_decrypted=None)
    encrypted = b'\x44\xa8\xdd\xd8' + b'\xab' * 40 + bytes([12]) + b'\xfa\xfa'
    assert looks_like_passthrough_payload(encrypted) is True      # the heuristic is fooled…
    assert _recover_silence_passthrough(packet, encrypted) is None  # …the real gate is not


def test_dave_input_uses_extension_offset_not_hardcoded_eight():
    from plugins.discord.voice.dave_voice_patches import _dave_input_from_packet

    # Passthrough-style RTP extension: length=1 → 4-byte header, payload follows at offset 4.
    outer = b'\x00\x01\x00\x01' + b'\xfc\x03' + b'\xab' * 6 + bytes([8]) + b'\xfa\xfa'
    packet = SimpleNamespace(extended=True, _outer_decrypted=outer, _rtpsize=True)

    def update_extended_header(data):
        return 4

    packet.update_extended_header = update_extended_header
    assert _dave_input_from_packet(packet) == outer[4:]


def test_gen0_passthrough_recovery_rejects_encrypted_ciphertext():
    from plugins.discord.voice.dave_voice_patches import _recover_silence_passthrough

    packet = SimpleNamespace(padding=False, extended=True, _outer_decrypted=None)
    encrypted = b'\xf7\x5a\x09\x81' + b'\xab' * 50 + bytes([14]) + b'\xfa\xfa'
    assert _recover_silence_passthrough(packet, encrypted) is None


def test_silence_passthrough_recovery_rejects_non_fafa_payload():
    from plugins.discord.voice.dave_voice_patches import _recover_silence_passthrough

    packet = SimpleNamespace(padding=False, extended=False, _outer_decrypted=None)
    assert _recover_silence_passthrough(packet, b'\xf8\xff\xfe' + b'\xab' * 20) is None


def test_looks_like_opus_payload():
    from plugins.discord.voice.dave_voice_patches import looks_like_opus_payload

    assert looks_like_opus_payload(b'\xf8\xff\xfe') is True
    assert looks_like_opus_payload(b'\x32' * 20) is False


def test_forget_ssrc_evicts_both_maps():
    from plugins.discord.voice import dave_voice_patches as dvp

    dvp.note_ssrc_packet(777)
    dvp.mark_ssrc_decrypt_ready(777)
    assert dvp.is_ssrc_decrypt_ready(777)
    dvp.forget_ssrcs([777, 'bogus', None])
    assert 777 not in dvp._SSRC_DECRYPT_READY
    assert 777 not in dvp._SSRC_FIRST_SEEN
    assert not dvp.is_ssrc_decrypt_ready(777)


def test_rollover_patch_and_disconnect_evict_ssrc_state():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert 'forget_ssrc(old_ssrc)' in (root / 'voice' / 'pycord_patches.py').read_text(encoding='utf-8')
    assert 'forget_ssrcs(' in (root / 'transport' / 'discord_execution.py').read_text(encoding='utf-8')
