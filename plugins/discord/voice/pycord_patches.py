"""Runtime patches for py-cord voice receive (plugin-local, not Sapphire core)."""

from __future__ import annotations

import logging

from plugins.discord.voice.patch_registry import apply_patch

logger = logging.getLogger(__name__)

_APPLIED = False


def _safe_stop_recording(voice_client) -> None:
    if not voice_client:
        return
    try:
        if getattr(voice_client, 'is_recording', lambda: False)():
            voice_client.stop_recording()
    except Exception as exc:
        logger.debug('Ignored voice stop_recording error: %s', exc)


def apply_pycord_voice_patches() -> None:
    """Harden py-cord voice receive against DAVE/Opus glitches."""
    global _APPLIED
    if _APPLIED:
        return
    # Each patch reports through the registry (H11); none can raise out of here.
    apply_patch('opus_decoder_resilience', _patch_opus_decoder)
    apply_patch('opus_pcm_dave_double_decrypt', _patch_opus_pcm_dave_double_decrypt)
    apply_patch('ssrc_rollover_decoder_cleanup', _patch_ssrc_rollover)
    apply_patch('packet_router_stop_guard', _patch_packet_routers)
    apply_patch('rekey_reader_resync', _patch_rekey_reader_resync)
    apply_patch('aead_log_flood_limiter', _patch_aead_log_flood)
    _APPLIED = True


def _patch_opus_decoder() -> None:
    """Keep packet timeline aligned when Opus decode fails mid-stream."""
    from discord.opus import Decoder, OpusError, PacketDecoder
    if getattr(PacketDecoder.pop_data, '_discord_cognitive_patched', False):
        return
    original = PacketDecoder.pop_data
    from plugins.discord.voice.dave_voice_patches import OPUS_SILENCE

    def pop_data(self, *, timeout: float = 0):
        try:
            return original(self, timeout=timeout)
        except OpusError as exc:
            logger.debug('OpusError in pop_data, resetting decoder: %s', exc)
            if self._decoder is not None:
                self._decoder = Decoder()
            packet = self._make_fakepacket()
            pcm = self._decoder.decode(OPUS_SILENCE, fec=False)
            from discord.object import Object
            from discord.voice import VoiceData

            member = self._get_cached_member()
            if member is None and self._cached_id:
                member = Object(id=self._cached_id)
            return VoiceData(packet, member, pcm=pcm)

    pop_data._discord_cognitive_patched = True
    PacketDecoder.pop_data = pop_data
    logger.info('Applied py-cord voice OpusError resilience patch')


def _decode_opus_payload(decoder, data, *, fec: bool = False):
    from discord.opus import Decoder, OpusError

    from plugins.discord.voice.dave_voice_patches import OPUS_SILENCE, _opus_pcm_peak

    payload = data if data else OPUS_SILENCE
    try:
        pcm = decoder.decode(payload, fec=fec)
        if _opus_pcm_peak(pcm) >= 30000:
            logger.debug('Opus decode saturated (%s), substituting silence', _opus_pcm_peak(pcm))
            fresh = Decoder()
            return fresh, fresh.decode(OPUS_SILENCE, fec=False)
        return decoder, pcm
    except OpusError as exc:
        logger.debug('Opus decode failed, resetting decoder: %s', exc)
        fresh = Decoder()
        return fresh, fresh.decode(OPUS_SILENCE, fec=False)


def _patch_ssrc_rollover() -> None:
    """Destroy stale per-SSRC decoders when Discord assigns a user a new SSRC."""
    from discord.voice.client import VoiceClient
    if getattr(VoiceClient._add_ssrc, '_discord_cognitive_rollover', False):
        return
    original = VoiceClient._add_ssrc

    def _add_ssrc(self, user_id: int, ssrc: int) -> None:
        old_ssrc = self._id_to_ssrc.get(user_id)
        if old_ssrc is not None and old_ssrc != ssrc and self._reader is not None:
            logger.info(
                'Destroying stale voice decoder ssrc=%s for user %s (new ssrc=%s)',
                old_ssrc,
                user_id,
                ssrc,
            )
            self._reader.packet_router.destroy_decoder(old_ssrc)
            try:
                from plugins.discord.voice.dave_voice_patches import forget_ssrc
                forget_ssrc(old_ssrc)
            except Exception:
                pass
        original(self, user_id, ssrc)

    _add_ssrc._discord_cognitive_rollover = True
    VoiceClient._add_ssrc = _add_ssrc
    logger.info('Applied py-cord SSRC rollover decoder cleanup patch (discord_cognitive)')


def _patch_opus_pcm_dave_double_decrypt() -> None:
    """Stop py-cord from DAVE-decrypting decoded PCM during passthrough windows.

    PacketDecryptor already outputs Opus; Opus decode yields PCM. When
    set_passthrough_mode() is active, py-cord's PacketDecoder._decode_packet
    runs dave.decrypt() on that PCM again, which corrupts every frame.
    """
    from discord.opus import PacketDecoder
    if getattr(PacketDecoder._decode_packet, '_discord_cognitive_skip_pcm_dave', False):
        return

    def _decode_packet(self, packet):
        assert self._decoder is not None
        other_code = True
        pcm = None
        if packet:
            other_code = False
            self._decoder, pcm = _decode_opus_payload(self._decoder, packet.decrypted_data, fec=False)
        if other_code:
            next_packet = self._buffer.peek_next()
            if next_packet is not None:
                self._decoder, pcm = _decode_opus_payload(
                    self._decoder,
                    next_packet.decrypted_data,
                    fec=True,
                )
                return (packet, pcm)
            self._decoder, pcm = _decode_opus_payload(self._decoder, None, fec=False)
        return (packet, pcm)

    _decode_packet._discord_cognitive_skip_pcm_dave = True
    PacketDecoder._decode_packet = _decode_packet
    logger.info('Applied py-cord opus PCM double-decrypt skip patch (discord_cognitive)')


class AeadFloodFilter(logging.Filter):
    """Rate-limit py-cord's per-packet AEAD failure logs.

    During bot TTS these fire at media rate WITH tracebacks — hundreds of
    journal lines per second that bury every useful voice log. Pass one
    through per window, count the rest, report the count on the next pass.
    """

    _discord_cognitive_aead_limit = True
    WINDOW_SECONDS = 5.0
    _MARKERS = ('Critical error at AEAD', 'CryptoError while decoding')

    def __init__(self):
        super().__init__()
        self._allowed_at = 0.0
        self._suppressed = 0

    def filter(self, record):
        message = str(record.msg)
        if not any(marker in message for marker in self._MARKERS):
            return True
        import time as _time
        now = _time.monotonic()
        if now >= self._allowed_at:
            self._allowed_at = now + self.WINDOW_SECONDS
            if self._suppressed:
                logger.warning(
                    'AEAD decrypt failures: %s more suppressed in last %ss window',
                    self._suppressed,
                    self.WINDOW_SECONDS,
                )
                self._suppressed = 0
            return True
        self._suppressed += 1
        return False


def _patch_aead_log_flood() -> None:
    target = logging.getLogger('discord.voice.receive.reader')
    if any(getattr(f, '_discord_cognitive_aead_limit', False) for f in target.filters):
        return
    target.addFilter(AeadFloodFilter())
    logger.info('Applied AEAD decrypt log flood limiter (discord_cognitive)')


def _patch_rekey_reader_resync() -> None:
    """Push mid-session transport re-keys into the active receive decryptor.

    py-cord's load_secret_key stores the new key on the connection state but
    never calls AudioReader.update_secret_key, so after any mid-session
    session_description (voice resume, DAVE transition) the receive box keeps
    the dead key: every packet fails outer AEAD ('Critical error at AEAD:
    Decryption failed.' flood) and the bot goes permanently deaf while still
    able to speak. Observed live 2026-08-01, seconds after first TTS reply.
    """
    from discord.voice.gateway import VoiceWebSocket
    if getattr(VoiceWebSocket.load_secret_key, '_discord_cognitive_rekey', False):
        return
    original = VoiceWebSocket.load_secret_key

    async def load_secret_key(self, data):
        await original(self, data)
        try:
            client = getattr(self.state, 'client', None)
            reader = getattr(client, '_reader', None) if client else None
            key = getattr(self.state, 'secret_key', None)
            if reader and key:
                reader.update_secret_key(bytes(key))
                logger.info('Receive decryptor re-keyed after new session description')
        except Exception:
            logger.exception('Receive decryptor re-key sync failed')

    load_secret_key._discord_cognitive_rekey = True
    VoiceWebSocket.load_secret_key = load_secret_key
    logger.info('Applied py-cord receive re-key resync patch (discord_cognitive)')


def _patch_packet_routers() -> None:
    from discord.voice.receive.router import PacketRouter, SinkEventRouter
    if not getattr(PacketRouter.run, '_discord_cognitive_patched', False):

        def packet_run_replacement(self):
            try:
                self._do_run()
            except Exception as exc:
                # WARNING, not debug: this path silently kills recording — the
                # bot goes deaf with no journal trace at default log level.
                logger.warning('PacketRouter loop died (recording stops): %s', exc, exc_info=exc)
                self.reader.error = exc
            finally:
                _safe_stop_recording(getattr(self.reader, 'client', None))
                self.waiter.clear()

        packet_run_replacement._discord_cognitive_patched = True
        PacketRouter.run = packet_run_replacement
    if not getattr(SinkEventRouter.run, '_discord_cognitive_patched', False):

        def sink_run_replacement(self):
            try:
                self._do_run()
            except Exception as exc:
                logger.warning('SinkEventRouter loop died (recording stops): %s', exc, exc_info=exc)
                self.reader.error = exc
                _safe_stop_recording(getattr(self.reader, 'client', None))

        sink_run_replacement._discord_cognitive_patched = True
        SinkEventRouter.run = sink_run_replacement
    logger.info('Applied py-cord voice router stop_recording guard patch')
