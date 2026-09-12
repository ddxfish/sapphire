"""DAVE voice-receive fixes for py-cord builds before the upstream decrypt patch."""

from __future__ import annotations

import inspect
import logging
import os
import struct
import time

logger = logging.getLogger(__name__)

OPUS_SILENCE = b'\xf8\xff\xfe'
_OPUS_SILENCE = OPUS_SILENCE
global _DAVE_PATCHED
global _PATCH_MODE
_DAVE_PATCHED = False
_PATCH_MODE = 'none'
_SSRC_DECRYPT_READY: set[int] = set()
_SSRC_FIRST_SEEN: dict[int, float] = {}
_SSRC_READY_TIMEOUT = 3.0


def patch_mode() -> str:
    return _PATCH_MODE


def note_ssrc_packet(ssrc: int) -> None:
    """Record first time we see an SSRC (for decrypt-ready timeout)."""
    key = int(ssrc)
    _SSRC_FIRST_SEEN.setdefault(key, time.monotonic())


def mark_ssrc_decrypt_ready(ssrc: int) -> None:
    _SSRC_DECRYPT_READY.add(int(ssrc))


def is_ssrc_decrypt_ready(ssrc: int) -> bool:
    """True after a successful DAVE decrypt, or after a short wait timeout."""
    key = int(ssrc)
    if key in _SSRC_DECRYPT_READY:
        return True
    seen = _SSRC_FIRST_SEEN.get(key)
    if seen is not None and (time.monotonic() - seen) >= _SSRC_READY_TIMEOUT:
        return True
    return False


def requested_dave_mode() -> str:
    """``DISCORD_VOICE_DAVE_MODE``: ``auto`` (default), ``legacy``, or ``upstream``."""
    mode = (os.environ.get('DISCORD_VOICE_DAVE_MODE') or 'auto').strip().lower()
    if mode in {'auto', 'legacy', 'upstream'}:
        return mode
    logger.warning('Unknown DISCORD_VOICE_DAVE_MODE=%r — using auto', mode)
    return 'auto'


def _upstream_dave_decrypt_fixed() -> bool:
    """True when installed py-cord already ships the 6e71bff+ DAVE receive fix."""
    try:
        from discord.voice.receive.reader import PacketDecryptor
        source = inspect.getsource(PacketDecryptor.decrypt_rtp)
    except (ImportError, OSError, TypeError):
        return False
    return 'UnencryptedWhenPassthroughDisabled' in source and 'dave.decrypt' in source


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
    return _OPUS_SILENCE


def looks_like_passthrough_payload(data: bytes, *, padding_flag: bool = False) -> bool:
    """True when payload has the DAVE supplemental trailer (0xFAFA).

    Encrypted DAVE frames also end in 0xFAFA — never use this alone to skip
    ``dave.decrypt()``; only for post-failure recovery on silence frames.
    """
    if not data or len(data) < 3:
        return False
    trimmed = _strip_rtp_padding(data, padding_flag=padding_flag)
    return trimmed[-2:] == b'\xfa\xfa'


def opus_decodable(data: bytes) -> bool:
    """True when libopus accepts the payload as a structured Opus packet."""
    return opus_packet_parses(data)


def opus_packet_parses(data: bytes) -> bool:
    if not data:
        return False
    if data == _OPUS_SILENCE:
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
    if data == _OPUS_SILENCE:
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
    try:
        from discord.voice.state import VoiceConnectionState
    except ImportError:
        return
    if getattr(VoiceConnectionState, '_discord_cognitive_ssrc_patch', False):
        return

    def ssrc_user_map(self):
        return self.client._ssrc_to_id

    VoiceConnectionState.ssrc_user_map = property(ssrc_user_map)
    VoiceConnectionState._discord_cognitive_ssrc_patch = True
    logger.info('Applied DAVE ssrc_user_map patch (discord_cognitive)')


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


def _raw_payload_from_packet(packet) -> bytes | None:
    """Reconstruct RTP payload slice passed to ``dave.decrypt`` (extension-aware)."""
    return _dave_input_from_packet(packet)


def _opus_pcm_peak(pcm: bytes) -> int:
    if not pcm:
        return 0
    samples = struct.unpack(f'<{len(pcm) // 2}h', pcm)
    return max((abs(sample) for sample in samples), default=0)


def _dave_frame_generation(dave_input: bytes) -> int:
    try:
        from discord.voice.receive.reader import PacketDecryptor

        return int(PacketDecryptor._parse_dave_generation(dave_input))
    except Exception:
        return -1


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


def _opus_decode_peak(data: bytes) -> int:
    if not data or data == _OPUS_SILENCE:
        return 0
    try:
        from discord.opus import Decoder

        pcm = Decoder().decode(data, fec=False)
    except Exception:
        return 0
    return _opus_pcm_peak(pcm)


def _opus_output_acceptable(data: bytes) -> bool:
    """Reject saturated decrypt output; allow DTX/silence Opus packets."""
    if not data or data == _OPUS_SILENCE:
        return False
    peak = _opus_decode_peak(data)
    if peak >= 28000:
        return False
    if peak > 0:
        return True
    return is_valid_opus_packet(data)


def _opus_decodes_clean(data: bytes) -> bool:
    if not data or data == _OPUS_SILENCE:
        return False
    peak = _opus_decode_peak(data)
    return 0 < peak < 28000


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


def _recover_gen0_passthrough(packet, raw_payload: bytes) -> bytes | None:
    """Deprecated alias — generation alone cannot distinguish passthrough vs encrypted."""
    return _recover_silence_passthrough(packet, raw_payload)


def apply_dave_decrypt_enhance_patch() -> None:
    """Validate decrypt output and recover true passthrough on silence."""
    try:
        from discord.voice.receive.reader import PacketDecryptor
    except ImportError:
        return
    upstream = PacketDecryptor.decrypt_rtp
    if getattr(upstream, '_discord_cognitive_enhanced', False):
        return

    def decrypt_rtp_enhanced(self, packet):
        ssrc = getattr(packet, 'ssrc', None)
        if ssrc is not None:
            note_ssrc_packet(int(ssrc))
        result = upstream(self, packet)
        if result and result != _OPUS_SILENCE:
            if _opus_output_acceptable(result):
                if ssrc is not None:
                    mark_ssrc_decrypt_ready(int(ssrc))
                return result
            packet.decrypted_data = _OPUS_SILENCE
            logger.debug(
                'DAVE decrypt output rejected ssrc=%s seq=%s head=%s',
                ssrc,
                getattr(packet, 'sequence', '?'),
                result[:4].hex(),
            )
            result = _OPUS_SILENCE
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

    decrypt_rtp_enhanced._discord_cognitive_enhanced = True
    PacketDecryptor.decrypt_rtp = decrypt_rtp_enhanced
    logger.info('Applied DAVE decrypt enhance patch (discord_cognitive)')


def apply_dave_silence_passthrough_patch() -> None:
    """Deprecated — use apply_dave_decrypt_enhance_patch."""
    apply_dave_decrypt_enhance_patch()


def apply_dave_opus_validate_patch() -> None:
    """Deprecated — use apply_dave_decrypt_enhance_patch."""
    apply_dave_decrypt_enhance_patch()


def apply_dave_passthrough_fallback_patch() -> None:
    """Deprecated — passthrough recovery injected ciphertext; use opus validate instead."""
    apply_dave_opus_validate_patch()


def apply_dave_unified_decrypt_patch() -> None:
    """Deprecated — encrypted DAVE frames also end in 0xFAFA; do not strip pre-decrypt."""
    apply_dave_passthrough_fallback_patch()


def apply_dave_bruteforce_patch() -> None:
    """Deprecated alias — unified decrypt supersedes this."""
    apply_dave_unified_decrypt_patch()


def apply_dave_supplement_patches() -> None:
    """Optional passthrough recovery when upstream returns Opus silence only."""
    try:
        from discord.voice.receive.reader import PacketDecryptor
    except ImportError:
        return
    if getattr(PacketDecryptor.decrypt_rtp, '_discord_cognitive_supplement', False):
        return
    original = PacketDecryptor.decrypt_rtp

    def decrypt_rtp_supplement(self, packet):
        result = original(self, packet)
        if result and result != _OPUS_SILENCE:
            return result
        outer = getattr(packet, '_outer_decrypted', None)
        if not outer and not looks_like_passthrough_payload(result or b''):
            return result
        recovered = recover_passthrough_opus(packet, result, outer)
        if recovered != _OPUS_SILENCE:
            packet.decrypted_data = recovered
            return recovered
        return result

    decrypt_rtp_supplement._discord_cognitive_supplement = True
    PacketDecryptor.decrypt_rtp = decrypt_rtp_supplement
    logger.info('Applied DAVE supplement passthrough patch (discord_cognitive)')


def apply_dave_voice_patches() -> None:
    global _DAVE_PATCHED
    global _PATCH_MODE
    if _DAVE_PATCHED:
        return
    try:
        import davey
        from discord.voice.receive.reader import PacketDecryptor
    except ImportError as exc:
        logger.warning('DAVE voice patches skipped (missing dependency): %s', exc)
        _PATCH_MODE = 'none'
        return

    apply_ssrc_user_map_patch()

    if getattr(PacketDecryptor.decrypt_rtp, '_discord_cognitive_dave_patched', False):
        _DAVE_PATCHED = True
        _PATCH_MODE = 'legacy'
        return

    mode = requested_dave_mode()
    upstream_fixed = _upstream_dave_decrypt_fixed()

    if upstream_fixed and mode in {'auto', 'upstream', 'legacy'}:
        if mode == 'legacy':
            logger.warning(
                'DISCORD_VOICE_DAVE_MODE=legacy ignored — py-cord already ships the upstream '
                'DAVE receive fix; using upstream decrypt with ssrc patch only'
            )
        apply_dave_decrypt_enhance_patch()
        _PATCH_MODE = 'upstream+enhance'
        _DAVE_PATCHED = True
        logger.info('Using upstream DAVE decrypt with enhance patch (mode=%s)', mode)
        return

    if mode == 'legacy':
        logger.info('DISCORD_VOICE_DAVE_MODE=legacy — applying full legacy DAVE decrypt patches')
    PacketDecryptor._dave_success = {}
    PacketDecryptor._dave_failure = {}
    PacketDecryptor._dave_consecutive_failures = {}
    PacketDecryptor._dave_last_success_time = {}
    PacketDecryptor._dave_passthrough_recovered = {}

    def _decrypt_rtp_aead_xchacha20_poly1305_rtpsize(self, packet):
        from nacl.exceptions import CryptoError

        packet.adjust_rtpsize()
        nonce = packet.nonce + b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        try:
            result = self.box.decrypt(
                packet.decrypted_data or packet.data,
                bytes(packet.header),
                nonce,
            )
        except Exception as exc:
            logger.error('Critical error at AEAD: %s', exc)
            raise CryptoError(exc) from exc
        if packet.extended:
            offset = packet.update_extended_header(result)
        else:
            offset = 0
        packet._outer_decrypted = result
        return result[offset:]

    def decrypt_rtp(self, packet):
        state = self.client._connection
        dave = state.dave_session
        raw_payload = self._decryptor_rtp(packet)
        if packet.extended:
            dave_input = raw_payload
        else:
            dave_input = getattr(packet, '_outer_decrypted', raw_payload)
        packet.decrypted_data = None
        if dave is not None and dave.ready:
            uid = state.ssrc_user_map.get(packet.ssrc)
            if uid:
                try:
                    packet.decrypted_data = dave.decrypt(
                        uid,
                        davey.MediaType.audio,
                        dave_input,
                    )
                    self._dave_success[packet.ssrc] = self._dave_success.get(packet.ssrc, 0) + 1
                    self._dave_consecutive_failures[packet.ssrc] = 0
                    self._dave_last_success_time[packet.ssrc] = time.perf_counter()
                except Exception as exc:
                    fail_count = self._dave_failure.get(packet.ssrc, 0) + 1
                    self._dave_failure[packet.ssrc] = fail_count
                    consec = self._dave_consecutive_failures.get(packet.ssrc, 0) + 1
                    self._dave_consecutive_failures[packet.ssrc] = consec
                    if 'UnencryptedWhenPassthroughDisabled' in str(exc):
                        packet.decrypted_data = recover_passthrough_opus(
                            packet,
                            raw_payload,
                            dave_input,
                        )
                        if packet.decrypted_data != _OPUS_SILENCE:
                            self._dave_passthrough_recovered[packet.ssrc] = (
                                self._dave_passthrough_recovered.get(packet.ssrc, 0) + 1
                            )
                    else:
                        if consec == 1 or fail_count <= 3:
                            logger.warning(
                                'DAVE decrypt fail ssrc=%s uid=%s seq=%s err=%s',
                                packet.ssrc,
                                uid,
                                getattr(packet, 'sequence', '?'),
                                exc,
                            )
                        packet.decrypted_data = _OPUS_SILENCE
        if packet.decrypted_data is None:
            if dave is None:
                if packet.extended:
                    offset = packet.update_extended_header(raw_payload)
                    packet.decrypted_data = raw_payload[offset:]
                    return packet.decrypted_data
                packet.decrypted_data = raw_payload
                pass
                return packet.decrypted_data
            packet.decrypted_data = _OPUS_SILENCE
        return packet.decrypted_data

    decrypt_rtp._discord_cognitive_dave_patched = True
    _decrypt_rtp_aead_xchacha20_poly1305_rtpsize._discord_cognitive_dave_patched = True
    PacketDecryptor.decrypt_rtp = decrypt_rtp
    PacketDecryptor._decrypt_rtp_aead_xchacha20_poly1305_rtpsize = _decrypt_rtp_aead_xchacha20_poly1305_rtpsize
    _DAVE_PATCHED = True
    _PATCH_MODE = 'legacy'
    logger.info('Applied legacy DAVE voice receive decrypt patches (discord_cognitive)')
