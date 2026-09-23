"""DAVE voice-receive enhancements on top of py-cord's upstream decrypt.

The legacy full decrypt branch (for builds before the upstream fix) was dead
by the pinned py-cord commit and is gone (2026-09-13); a build older than the
pin gets a loud warning and the enhance patch, never a silent second decryptor.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import time

from plugins.discord.voice.patch_registry import apply_patch

logger = logging.getLogger(__name__)

OPUS_SILENCE = b'\xf8\xff\xfe'
global _DAVE_PATCHED
global _PATCH_MODE
_DAVE_PATCHED = False
_PATCH_MODE = 'none'
_SSRC_DECRYPT_READY: set[int] = set()
_SSRC_FIRST_SEEN: dict[int, float] = {}
_SSRC_READY_TIMEOUT = 3.0
_TRANSPORT_FAIL_WINDOW = 5.0
_transport_fail_state = {'allowed_at': 0.0, 'suppressed': 0}


def patch_mode() -> str:
    return _PATCH_MODE


def note_ssrc_packet(ssrc: int) -> None:
    """Record first time we see an SSRC (for decrypt-ready timeout)."""
    key = int(ssrc)
    _SSRC_FIRST_SEEN.setdefault(key, time.monotonic())


def mark_ssrc_decrypt_ready(ssrc: int) -> None:
    _SSRC_DECRYPT_READY.add(int(ssrc))


def forget_ssrc(ssrc) -> None:
    """Evict one SSRC (rollover / leave) — these maps grew for the process life."""
    try:
        key = int(ssrc)
    except (TypeError, ValueError):
        return
    _SSRC_DECRYPT_READY.discard(key)
    _SSRC_FIRST_SEEN.pop(key, None)


def forget_ssrcs(ssrcs) -> None:
    for ssrc in list(ssrcs or ()):
        forget_ssrc(ssrc)


def is_ssrc_decrypt_ready(ssrc: int) -> bool:
    """True after a successful DAVE decrypt, or after a short wait timeout."""
    key = int(ssrc)
    if key in _SSRC_DECRYPT_READY:
        return True
    seen = _SSRC_FIRST_SEEN.get(key)
    if seen is not None and (time.monotonic() - seen) >= _SSRC_READY_TIMEOUT:
        return True
    return False


def note_transport_decrypt_failure(packet, exc, *, now: float | None = None) -> str | None:
    """One line per window naming the packet the OUTER (transport AEAD) decrypt
    refused — ssrc / seq / payload type / extension / length — so a burst can be
    traced to its sender. py-cord drops the packet and carries on; this only
    answers "who sent it". Returns the line when it logged, else None."""
    now = time.monotonic() if now is None else now
    state = _transport_fail_state
    if now < state['allowed_at']:
        state['suppressed'] += 1
        return None
    state['allowed_at'] = now + _TRANSPORT_FAIL_WINDOW
    more = state['suppressed']
    state['suppressed'] = 0
    data = getattr(packet, 'data', b'') or b''
    line = ('[DISCORD] voice packet failed transport decrypt — dropped: ssrc=%s seq=%s ts=%s pt=%s ext=%s pad=%s '
            'len=%s (%s)%s' % (
                getattr(packet, 'ssrc', '?'), getattr(packet, 'sequence', '?'), getattr(packet, 'timestamp', '?'),
                getattr(packet, 'payload_type', '?'), getattr(packet, 'extended', '?'), getattr(packet, 'padding', '?'),
                len(data), exc, f' +{more} more in the last {_TRANSPORT_FAIL_WINDOW:.0f}s' if more else ''))
    logger.warning(line)
    return line


def requested_dave_mode() -> str:
    """``DISCORD_VOICE_DAVE_MODE``: ``auto`` (default) or ``upstream``."""
    mode = (os.environ.get('DISCORD_VOICE_DAVE_MODE') or 'auto').strip().lower()
    if mode in {'auto', 'upstream'}:
        return mode
    logger.warning('Unknown DISCORD_VOICE_DAVE_MODE=%r — using auto', mode)
    return 'auto'


# The py-cord build that ships the upstream DAVE receive fix (voice_deps.INSTALL_HINT).
PINNED_PYCORD_COMMIT = '6e71bff'
_FIX_RELEASED_IN = (2, 8, 0)


def _pycord_version() -> str:
    try:
        import discord
        return str(getattr(discord, '__version__', '') or '')
    except ImportError:
        return ''


def _version_at_least(version: str, floor: tuple) -> bool:
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version or '')
    return bool(match) and tuple(int(x) for x in match.groups()) >= floor


def _upstream_dave_decrypt_fixed(version: str | None = None) -> bool:
    """True when the installed py-cord carries the upstream DAVE receive fix.

    Gated on the VERSION (M23), not inspect.getsource of a private method — a
    reformat of upstream's source used to flip this to False on a build that
    did not need the legacy decryptor at all.
    """
    version = _pycord_version() if version is None else version
    if PINNED_PYCORD_COMMIT in version:
        return True
    return _version_at_least(version, _FIX_RELEASED_IN)


def _strip_dave_supplemental_block(data: bytes) -> bytes | None:
    if len(data) < 3 or data[-2:] != b'\xfa\xfa':
        return None
    supp_size = data[-3]
    if supp_size < 3 or supp_size >= len(data):
        return None
    core = data[:-supp_size]
    return core if core else None


def _strip_rtp_padding(data: bytes, *, padding_flag: bool) -> bytes:
    if not data:
        return data
    if padding_flag:
        pad_n = data[-1]
        if 0 < pad_n < len(data):
            return data[:-pad_n]
    pad_n = data[-1]
    if 0 < pad_n < min(64, len(data)):
        trial = data[:-pad_n]
        if len(trial) >= 3 and trial[-2:] == b'\xfa\xfa':
            return trial
    return data


def recover_passthrough_opus(packet, *payloads: bytes) -> bytes:
    """Recover raw Opus from DAVE passthrough frames (requires 0xFAFA trailer)."""
    outer = getattr(packet, '_outer_decrypted', None)
    candidates = []
    for item in payloads:
        if not item:
            continue
        candidates.append(item)
    if outer:
        candidates.append(outer)
        if getattr(packet, 'extended', False) and len(outer) >= 4:
            try:
                ext_len = struct.unpack('>H', outer[2:4])[0]
                offset = 4 + ext_len * 4
                if 0 < offset < len(outer):
                    candidates.append(outer[offset:])
            except struct.error:
                pass
            if len(outer) > 8:
                candidates.append(outer[4:])
                candidates.append(outer[8:])
    padding_flag = bool(getattr(packet, 'padding', False))
    seen = set()
    for raw in candidates:
        if raw in seen:
            continue
        seen.add(raw)
        trimmed = _strip_rtp_padding(raw, padding_flag=padding_flag)
        opus = _strip_dave_supplemental_block(trimmed)
        if opus and len(opus) >= 1:
            return opus
    return OPUS_SILENCE


def looks_like_passthrough_payload(data: bytes, *, padding_flag: bool = False) -> bool:
    """True when payload has the DAVE supplemental trailer (0xFAFA).

    Encrypted DAVE frames also end in 0xFAFA — never use this alone to skip
    ``dave.decrypt()``; only for post-failure recovery on silence frames.
    """
    if not data or len(data) < 3:
        return False
    trimmed = _strip_rtp_padding(data, padding_flag=padding_flag)
    return trimmed[-2:] == b'\xfa\xfa'


def opus_packet_parses(data: bytes) -> bool:
    if not data:
        return False
    if data == OPUS_SILENCE:
        return True
    try:
        from discord.opus import Decoder
        import struct

        if data[0] & 248 == 248:
            frames = Decoder.packet_get_nb_frames(data)
            samples = Decoder.packet_get_samples_per_frame(data)
            if frames > 0 and samples > 0:
                Decoder().decode(data, fec=False)
                return True
        pcm = Decoder().decode(data, fec=False)
        if not pcm:
            return False
        decoded = struct.unpack(f'<{len(pcm)//2}h', pcm)
        return max(abs(sample) for sample in decoded) > 0
    except Exception:
        return False


def looks_like_opus_payload(data: bytes) -> bool:
    return bool(data) and data[0] & 248 == 248


def is_valid_opus_packet(data: bytes) -> bool:
    """True when data is Opus silence or parses as a real Opus packet."""
    if not data:
        return False
    if data == OPUS_SILENCE:
        return True
    if not looks_like_opus_payload(data):
        return False
    try:
        from discord.opus import Decoder

        frames = Decoder.packet_get_nb_frames(data)
        samples = Decoder.packet_get_samples_per_frame(data)
        return frames > 0 and samples > 0
    except Exception:
        return False


def apply_ssrc_user_map_patch() -> None:
    """Use VoiceClient._ssrc_to_id for DAVE decrypt (py-cord 7b2cbea fix)."""
    from discord.voice.state import VoiceConnectionState
    if getattr(VoiceConnectionState, '_discord_host_ssrc_patch', False):
        return

    def ssrc_user_map(self):
        return self.client._ssrc_to_id

    VoiceConnectionState.ssrc_user_map = property(ssrc_user_map)
    VoiceConnectionState._discord_host_ssrc_patch = True
    logger.info('Applied DAVE ssrc_user_map patch')


def _dave_input_from_packet(packet) -> bytes | None:
    """Derive the DAVE ciphertext slice from a decrypted RTP packet."""
    outer = getattr(packet, '_outer_decrypted', None)
    if not outer:
        return None
    offset = _extension_payload_offset(packet, outer)
    return outer[offset:]


def _extension_payload_offset(packet, outer: bytes) -> int:
    """Parse RTP extension length without mutating packet state."""
    if not getattr(packet, 'extended', False) or len(outer) < 4:
        return 0
    _profile, length = struct.unpack_from('>2sH', outer)
    offset = 4 + length * 4
    if getattr(packet, '_rtpsize', False):
        offset = max(0, offset - 4)
    return max(0, min(offset, len(outer)))


def _opus_pcm_peak(pcm: bytes) -> int:
    if not pcm:
        return 0
    try:
        import numpy as np
        return int(np.abs(np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype='<i2')).max()) if len(pcm) >= 2 else 0
    except ImportError:
        samples = struct.unpack(f'<{len(pcm) // 2}h', pcm)
        return max((abs(sample) for sample in samples), default=0)


def _strip_passthrough_like_upstream(packet, raw_payload: bytes) -> bytes | None:
    """Mirror py-cord passthrough strip (UnencryptedWhenPassthroughDisabled path)."""
    opus_data = raw_payload
    if getattr(packet, 'padding', False) and opus_data:
        pad_n = opus_data[-1]
        if 0 < pad_n < len(opus_data):
            opus_data = opus_data[:-pad_n]
    if len(opus_data) >= 3 and opus_data[-2:] == b'\xfa\xfa':
        supp_size = opus_data[-3]
        if 3 <= supp_size < len(opus_data):
            opus = opus_data[:-supp_size]
            if len(opus) >= 3:
                return opus
    return None


_PROBE_DECODER = None


def _opus_decode_peak(data: bytes) -> int:
    """Peak of a probe decode — the one discriminator between decrypted Opus
    and ciphertext garbage (which decodes saturated). One cached probe decoder
    instead of a fresh libopus decoder per packet (50/s per speaker); the real
    per-SSRC decoders downstream stay untouched."""
    global _PROBE_DECODER
    if not data or data == OPUS_SILENCE:
        return 0
    try:
        from discord.opus import Decoder

        if _PROBE_DECODER is None:
            _PROBE_DECODER = Decoder()
        pcm = _PROBE_DECODER.decode(data, fec=False)
    except Exception:
        _PROBE_DECODER = None
        return 0
    return _opus_pcm_peak(pcm)


def _opus_output_acceptable(data: bytes) -> bool:
    """Reject saturated decrypt output; allow DTX/silence Opus packets."""
    if not data or data == OPUS_SILENCE:
        return False
    peak = _opus_decode_peak(data)
    if peak >= 28000:
        return False
    if peak > 0:
        return True
    return is_valid_opus_packet(data)


def _passthrough_raw_payload(packet) -> bytes | None:
    """DAVE input slice matching py-cord's post-RTP-decrypt payload."""
    return _dave_input_from_packet(packet)


def _recover_silence_passthrough(packet, raw_payload: bytes | None = None) -> bytes | None:
    """Recover true passthrough when upstream DAVE decrypt returned silence.

    Requires a structurally valid Opus packet after strip so encrypted
    ciphertext (which also ends in 0xFAFA) is rejected.
    """
    raw = raw_payload or _passthrough_raw_payload(packet)
    if not raw:
        return None
    opus = _strip_passthrough_like_upstream(packet, raw)
    if not opus or not is_valid_opus_packet(opus):
        return None
    if _opus_decode_peak(opus) >= 28000:
        return None
    return opus


def apply_dave_decrypt_enhance_patch() -> None:
    """Validate decrypt output and recover true passthrough on silence."""
    from discord.voice.receive.reader import PacketDecryptor
    upstream = PacketDecryptor.decrypt_rtp
    if getattr(upstream, '_discord_host_enhanced', False):
        return

    def decrypt_rtp_enhanced(self, packet):
        ssrc = getattr(packet, 'ssrc', None)
        if ssrc is not None:
            note_ssrc_packet(int(ssrc))
        try:
            result = upstream(self, packet)
        except Exception as exc:
            if type(exc).__name__ == 'CryptoError':
                note_transport_decrypt_failure(packet, exc)
            raise
        if result and result != OPUS_SILENCE:
            if _opus_output_acceptable(result):
                if ssrc is not None:
                    mark_ssrc_decrypt_ready(int(ssrc))
                return result
            packet.decrypted_data = OPUS_SILENCE
            logger.debug(
                'DAVE decrypt output rejected ssrc=%s seq=%s head=%s',
                ssrc,
                getattr(packet, 'sequence', '?'),
                result[:4].hex(),
            )
            result = OPUS_SILENCE
        recovered = _recover_silence_passthrough(packet)
        if recovered:
            packet.decrypted_data = recovered
            if ssrc is not None:
                mark_ssrc_decrypt_ready(int(ssrc))
            logger.info(
                'DAVE silence passthrough recovered ssrc=%s seq=%s head=%s',
                ssrc,
                getattr(packet, 'sequence', '?'),
                recovered[:4].hex(),
            )
            return recovered
        return result

    decrypt_rtp_enhanced._discord_host_enhanced = True
    PacketDecryptor.decrypt_rtp = decrypt_rtp_enhanced
    logger.info('Applied DAVE decrypt enhance patch')


def apply_dave_voice_patches() -> None:
    global _DAVE_PATCHED
    global _PATCH_MODE
    if _DAVE_PATCHED:
        return
    try:
        import davey  # noqa: F401 — the decrypt library itself
        from discord.voice.receive.reader import PacketDecryptor  # noqa: F401
    except ImportError as exc:
        logger.warning('DAVE voice patches skipped (missing dependency): %s', exc)
        _PATCH_MODE = 'none'
        return

    apply_patch('dave_ssrc_user_map', apply_ssrc_user_map_patch)

    mode = requested_dave_mode()
    version = _pycord_version()
    if _upstream_dave_decrypt_fixed(version):
        _PATCH_MODE = 'upstream+enhance'
    else:
        logger.warning(
            'py-cord %s predates the pinned DAVE receive fix (%s) — voice receive may be garbled; '
            'reinstall per voice_deps.INSTALL_HINT',
            version or 'unknown', PINNED_PYCORD_COMMIT,
        )
        _PATCH_MODE = 'upstream+enhance(unverified)'
    apply_patch('dave_decrypt_enhance', apply_dave_decrypt_enhance_patch)
    _DAVE_PATCHED = True
    logger.info('Using upstream DAVE decrypt with enhance patch (mode=%s, py-cord %s)', mode, version or 'unknown')
