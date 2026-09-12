"""DiscordConversationSource — conversation source+sink over Discord voice transport.

Plugin-local clone of core/conversation/browser_source.py + twilio_source.py.
Uses ConversationDriver + SpeechGate from core as libraries; lifecycle is owned
by plugins.discord.voice.discord_conversation_runner (no core file edits).
"""
from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger(__name__)

_FRAME_BYTES = 512 * 2


class DiscordConversationSource:
    def __init__(
        self,
        driver,
        gate,
        playback_service,
        *,
        account_name: str,
        channel_id: str,
        speech_bridge=None,
        voice_transport=None,
    ):
        self.driver = driver
        self.gate = gate
        self.playback_service = playback_service
        self.speech_bridge = speech_bridge
        self.voice_transport = voice_transport
        self.account_name = account_name
        self.channel_id = str(channel_id)
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=128)
        self._pcm_buf = b''
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._playing = False
        self._audio_bytes_fed = 0

    def push_pcm(self, data: bytes, *, is_speech: bool | None = None) -> None:
        if not data:
            return
        item = (data, is_speech)
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass

    def start(self, *, start_playback: bool = True) -> None:
        self._stop_flag.clear()
        if self._running:
            self._audio_bytes_fed = 0
            if start_playback:
                result = self.playback_service.start(self.account_name, self.channel_id)
                if result.get('status') == 'error':
                    logger.warning(
                        'Discord streaming playback re-start failed for %s:%s: %s',
                        self.account_name,
                        self.channel_id,
                        result.get('error'),
                    )
            return
        self.gate.reset()
        self.driver.reset()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='discord-conv-src')
        self._thread.start()
        if start_playback:
            result = self.playback_service.start(self.account_name, self.channel_id)
            if result.get('status') == 'error':
                logger.warning(
                    'Discord streaming playback start failed for %s:%s: %s',
                    self.account_name,
                    self.channel_id,
                    result.get('error'),
                )
        logger.info('[DISCORD] conversation source started for %s:%s', self.account_name, self.channel_id)

    async def start_playback_async(self) -> dict:
        if not self.playback_service:
            return {'status': 'unavailable'}
        return await self.playback_service.start_async(self.account_name, self.channel_id)

    def close(self) -> None:
        self._running = False
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self.playback_service.stop(self.account_name, self.channel_id)
        except Exception as exc:
            logger.debug('Discord conversation playback stop: %s', exc)
        logger.info('[DISCORD] conversation source closed for %s:%s', self.account_name, self.channel_id)

    def _loop(self) -> None:
        while self._running:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(item, tuple):
                data, speech_hint = item
            else:
                data, speech_hint = item, None
            self._pcm_buf += data
            while len(self._pcm_buf) >= _FRAME_BYTES:
                frame = self._pcm_buf[:_FRAME_BYTES]
                self._pcm_buf = self._pcm_buf[_FRAME_BYTES:]
                try:
                    if speech_hint is None:
                        import numpy as np
                        is_sp = self.gate.is_speech(np.frombuffer(frame, dtype=np.int16))
                    else:
                        is_sp = bool(speech_hint)
                    self.driver.push_frame(frame, is_sp)
                except Exception as exc:
                    logger.error('[DISCORD] frame processing failed: %s', exc)

    def _ev_payload(self) -> dict:
        return {
            'surface': 'discord',
            'chat': getattr(self.driver, '_chat_name', None),
        }

    def feed_chunk(self, chunk: dict) -> None:
        if self._stop_flag.is_set() or not chunk:
            return
        audio_b64 = chunk.get('audio_b64')
        if not audio_b64:
            return
        if not self._playing:
            self._playing = True
        result = self.playback_service.feed_chunk(self.account_name, self.channel_id, chunk)
        if isinstance(result, dict) and result.get('status') == 'not_streaming':
            logger.error(
                '[DISCORD] feed_chunk dropped — streaming playback not active for %s:%s',
                self.account_name,
                self.channel_id,
            )
            return
        self._audio_bytes_fed += 1

    def interrupt_playback(self) -> None:
        """Cut in-flight playback without closing the source (driver re-arms per turn)."""
        self._stop_flag.set()
        try:
            self.playback_service.stop(self.account_name, self.channel_id)
        except Exception as exc:
            logger.debug('Discord conversation playback interrupt failed: %s', exc)
        self._playing = False

    def finish(self) -> None:
        if not self._stop_flag.is_set():
            self.playback_service.finish(self.account_name, self.channel_id)

    def stop(self) -> None:
        self.interrupt_playback()

    def wait(self, timeout: float = 180.0) -> None:
        self.playback_service.wait(self.account_name, self.channel_id, timeout=timeout)
        if self._audio_bytes_fed <= 0:
            self._batch_fallback_speak()
        self._playing = False

    def _batch_fallback_speak(self) -> None:
        text = self._latest_assistant_text()
        if not text or not self.speech_bridge or not self.voice_transport:
            if text:
                logger.warning(
                    '[DISCORD] streaming TTS produced no audio for %s:%s; batch fallback unavailable',
                    self.account_name,
                    self.channel_id,
                )
            return
        logger.info(
            '[DISCORD] streaming TTS empty for %s:%s — batch fallback (%s chars)',
            self.account_name,
            self.channel_id,
            len(text),
        )
        audio = self.speech_bridge.synthesize_speech(text)
        audio_bytes = audio.get('audio_bytes') if isinstance(audio, dict) else audio
        if not audio_bytes:
            logger.warning('[DISCORD] batch fallback synthesis returned no audio')
            return
        if not self._playing:
            self._playing = True
        result = self.voice_transport.play_audio_sync(
            self.account_name,
            self.channel_id,
            audio_bytes,
            audio_format='wav',
        )
        if result.get('status') == 'error':
            logger.warning('[DISCORD] batch fallback playback failed: %s', result.get('error'))

    def _latest_assistant_text(self) -> str:
        chat_name = getattr(self.driver, '_chat_name', None)
        system = getattr(self.driver, 'system', None)
        if not chat_name or not system:
            return ''
        sm = getattr(getattr(system, 'llm_chat', None), 'session_manager', None)
        if sm is None:
            return ''
        try:
            messages = sm.read_chat_messages(chat_name) or []
        except Exception:
            return ''
        for message in reversed(messages):
            if str(message.get('role') or '') != 'assistant':
                continue
            content = message.get('content')
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        parts.append(str(block.get('text') or ''))
                joined = ' '.join(part.strip() for part in parts if part.strip()).strip()
                if joined:
                    return joined
        return ''
