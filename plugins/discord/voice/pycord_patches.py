"""Runtime patches for py-cord voice receive (plugin-local, not Sapphire core)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

global _APPLIED
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
    _patch_opus_decoder()
    _patch_jitter_flush_decoder_reset()
    _patch_opus_pcm_dave_double_decrypt()
    _patch_ssrc_rollover()
    _patch_packet_routers()
    _APPLIED = True


def _patch_opus_decoder() -> None:
    """Keep packet timeline aligned when Opus decode fails mid-stream."""
    try:
        from discord.opus import Decoder, OpusError, PacketDecoder
    except ImportError:
        return
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


def _patch_jitter_flush_decoder_reset() -> None:
    """Keep stock jitter flush — custom queue/FEC changes scrambled packet order."""
    return


def _patch_ssrc_rollover() -> None:
    """Destroy stale per-SSRC decoders when Discord assigns a user a new SSRC."""
    try:
        from discord.voice.client import VoiceClient
    except ImportError:
        return
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
    try:
        from discord.opus import PacketDecoder
    except ImportError:
        return
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


def _patch_packet_routers() -> None:
    try:
        from discord.voice.receive.router import PacketRouter, SinkEventRouter
    except ImportError:
        return
    if not getattr(PacketRouter.run, '_discord_cognitive_patched', False):

        def packet_run_replacement(self):
            try:
                self._do_run()
            except Exception as exc:
                logger.debug('PacketRouter loop ended: %s', exc, exc_info=exc)
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
                logger.debug('SinkEventRouter loop ended: %s', exc, exc_info=exc)
                self.reader.error = exc
                _safe_stop_recording(getattr(self.reader, 'client', None))

        sink_run_replacement._discord_cognitive_patched = True
        SinkEventRouter.run = sink_run_replacement
    logger.info('Applied py-cord voice router stop_recording guard patch')
