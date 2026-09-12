"""Start/stop Discord voice recording and route utterances to perception."""

from __future__ import annotations

import asyncio
import logging
import time

from plugins.discord.models.voice import VoiceMode
from plugins.discord.sapphire.voice_prompt import format_voice_turn_text
from plugins.discord.transport.discord_audio import concat_wav_bytes
from plugins.discord.voice.voice_workers import VOICE_WORKER_POOL

logger = logging.getLogger(__name__)
_UTTERANCE_MERGE_SECONDS = 4.0
_MIN_UTTERANCE_SECONDS = 0.35


class VoiceListenerService:
    def __init__(
        self,
        *,
        voice_transport,
        voice_perception_service,
        voice_conversation_service=None,
        voice_turn_taking_service=None,
        conversation_runner=None,
        voice_session_service=None,
        settings_store=None,
    ):
        self.voice_transport = voice_transport
        self.voice_perception_service = voice_perception_service
        self.voice_conversation_service = voice_conversation_service
        self.voice_turn_taking_service = voice_turn_taking_service
        self.conversation_runner = conversation_runner
        self.voice_session_service = voice_session_service
        self.settings_store = settings_store
        self._sessions = {}
        self._merge_pending = {}

    def _listening_params(self, session) -> tuple[float, float]:
        silence_seconds = 2.5
        min_duration_seconds = _MIN_UTTERANCE_SECONDS
        if self.settings_store:
            settings = self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
            if settings:
                silence_seconds = max(1.8, float(getattr(settings.voice, 'min_silence_seconds', 1.0)) + 1.2)
        return silence_seconds, min_duration_seconds

    def should_listen(self, session) -> bool:
        settings = (
            self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
            if self.settings_store
            else None
        )
        if settings and (not settings.voice.enabled or settings.voice.emergency_disabled):
            return False
        mode = session.mode
        mode_value = mode.value if isinstance(mode, VoiceMode) else str(mode)
        if mode_value == VoiceMode.LISTEN_ONLY.value:
            return bool(settings and settings.voice.transcription_enabled)
        if mode_value in (
            VoiceMode.TRANSCRIBE_ONLY.value,
            VoiceMode.SUMMARIZE_ONLY.value,
            VoiceMode.CONVERSATIONAL.value,
        ):
            return True
        return bool(settings and settings.voice.transcription_enabled)

    def _use_core_conversation(self, session) -> bool:
        mode = session.mode
        mode_value = mode.value if isinstance(mode, VoiceMode) else str(mode)
        if mode_value != VoiceMode.CONVERSATIONAL.value:
            return False
        if not self.conversation_runner:
            return False
        settings = (
            self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
            if self.settings_store
            else None
        )
        if settings and not getattr(settings.voice, 'conversation_core_enabled', True):
            return False
        if settings and (
            not settings.voice.enabled
            or settings.voice.emergency_disabled
            or not settings.voice.speaking_enabled
        ):
            return False
        return True

    def _conversation_runner_ok(self, result: dict) -> bool:
        return result.get('status') in ('active', 'already_active')

    def _frame_feed_listen_kwargs(self, session) -> dict:
        if not self.conversation_runner:
            return {}
        frame_feed = self.conversation_runner.frame_feed_for(session.session_id)
        if not frame_feed:
            return {}
        session_id = session.session_id

        def _pcm_barge_in(pcm_stereo: bytes, speech: bool) -> None:
            runner = self.conversation_runner
            if not runner or not runner.is_active(session_id):
                return
            if runner.interrupt_active_turn(session_id):
                logger.info('[DISCORD] PCM barge-in interrupted session=%s', session_id)
            with runner._lock:
                rec = runner._sessions.get(session_id)
            if not rec:
                return
            try:
                from core.conversation.engine import RESPONDING
                if getattr(rec['driver'].engine, 'state', None) == RESPONDING:
                    frame_feed.push_stereo_pcm(pcm_stereo, is_speech=speech)
            except Exception:
                pass

        def on_pcm_frame(user_id, pcm_stereo, rms, is_speech=None):
            del user_id, rms
            runner = self.conversation_runner
            if not runner or not runner.is_active(session_id):
                return
            if not is_speech:
                return
            if not runner.is_turn_active(session_id):
                return
            now = time.monotonic()
            last = float(getattr(session, '_last_pcm_barge_mono', 0.0) or 0.0)
            if now - last < 0.15:
                return
            session._last_pcm_barge_mono = now
            # sink.write runs on py-cord's PacketRouter thread while holding the
            # router lock; sync transport interrupt would deadlock the asyncio loop.
            VOICE_WORKER_POOL.submit(_pcm_barge_in, bytes(pcm_stereo), bool(is_speech))

        return {'on_pcm_frame': on_pcm_frame}

    def _conversation_listen_kwargs(self, session) -> dict:
        if not self._use_core_conversation(session):
            return {}
        result = self.conversation_runner.start(session)
        if not self._conversation_runner_ok(result):
            logger.warning(
                'Discord conversation runner not started for %s:%s: %s',
                session.account_name,
                session.channel_id,
                result,
            )
            return {}
        if result.get('status') == 'active':
            session._conv_runner_was_active = True
        return self._frame_feed_listen_kwargs(session)

    async def _conversation_listen_kwargs_async(self, session) -> dict:
        if not self._use_core_conversation(session):
            return {}
        result = await self.conversation_runner.start_async(session)
        if not self._conversation_runner_ok(result):
            logger.warning(
                'Discord conversation runner not started for %s:%s: %s',
                session.account_name,
                session.channel_id,
                result,
            )
            return {}
        if result.get('status') == 'active':
            session._conv_runner_was_active = True
        return self._frame_feed_listen_kwargs(session)

    def _ensure_conversation_runner_for_utterance(self, session) -> bool:
        if not self._use_core_conversation(session) or not self.conversation_runner:
            return False
        if self.conversation_runner.is_active(session.session_id):
            return True
        result = self.conversation_runner.ensure_started(session)
        if not self._conversation_runner_ok(result):
            logger.warning(
                'Discord conversation runner unavailable for utterance %s:%s: %s',
                session.account_name,
                session.channel_id,
                result,
            )
            return False
        session._conv_runner_was_active = True
        return True

    def _submit_conversation_turn(self, session, text: str, *, speaker_name: str = '') -> dict:
        if not text or not self.conversation_runner:
            return {'status': 'skipped'}
        if not self._ensure_conversation_runner_for_utterance(session):
            return {'status': 'runner_unavailable'}
        self.conversation_runner.interrupt_active_turn(session.session_id)
        labeled = format_voice_turn_text(text, speaker_name=speaker_name)
        return self.conversation_runner.submit_turn_text(session.session_id, labeled)

    def _ensure_core_runner(self, session) -> None:
        if not self._use_core_conversation(session) or not self.conversation_runner:
            return
        if self.conversation_runner.is_active(session.session_id):
            return
        was_active = bool(getattr(session, '_conv_runner_was_active', False))
        result = self.conversation_runner.start(session)
        if not self._conversation_runner_ok(result):
            logger.warning(
                'Discord conversation runner recovery failed for %s:%s: %s',
                session.account_name,
                session.channel_id,
                result,
            )
            return
        session._conv_runner_was_active = True
        if was_active and self.voice_session_service:
            self.voice_session_service.note_reconnect(session.session_id)

    async def _ensure_core_runner_async(self, session) -> None:
        if not self._use_core_conversation(session) or not self.conversation_runner:
            return
        if self.conversation_runner.is_active(session.session_id):
            return
        was_active = bool(getattr(session, '_conv_runner_was_active', False))
        result = await self.conversation_runner.start_async(session)
        if not self._conversation_runner_ok(result):
            logger.warning(
                'Discord conversation runner recovery failed for %s:%s: %s',
                session.account_name,
                session.channel_id,
                result,
            )
            return
        session._conv_runner_was_active = True
        if was_active and self.voice_session_service:
            self.voice_session_service.note_reconnect(session.session_id)

    def _bind_session(self, session, *, loop=None) -> tuple[tuple[str, str], object]:
        key = (session.account_name, str(session.channel_id))
        self._sessions[key] = session
        session._utterance_loop = loop

        def on_utterance(user_id, speaker_name, wav_bytes):
            VOICE_WORKER_POOL.submit(
                self._handle_utterance,
                session.account_name,
                str(session.channel_id),
                user_id,
                speaker_name,
                wav_bytes,
            )

        return key, on_utterance

    def _log_start_result(self, session, result: dict, *, ensure_runner: bool = True) -> dict:
        status = result.get('status', '')
        if ensure_runner and status in ('listening', 'already_listening'):
            self._ensure_core_runner(session)
        if status == 'listening':
            settings_mode = None
            if self.settings_store:
                settings = self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
                settings_mode = getattr(settings.voice, 'mode', None) if settings else None
            session_mode = session.mode.value if hasattr(session.mode, 'value') else session.mode
            logger.info(
                'Voice listener started for %s:%s (session_mode=%s settings_mode=%s)',
                session.account_name,
                session.channel_id,
                session_mode,
                settings_mode,
            )
            return result
        if status == 'already_listening':
            logger.debug('Voice listener already active for %s:%s', session.account_name, session.channel_id)
            return result
        logger.warning('Voice listener not started for %s:%s: %s', session.account_name, session.channel_id, result)
        return result

    async def _log_start_result_async(self, session, result: dict) -> dict:
        status = result.get('status', '')
        if status in ('listening', 'already_listening'):
            await self._ensure_core_runner_async(session)
        return self._log_start_result(session, result, ensure_runner=False)

    def start(self, session, *, loop=None) -> dict:
        if not self.should_listen(session):
            return {'status': 'skipped', 'reason': 'listen_disabled'}
        _key, on_utterance = self._bind_session(session, loop=loop)
        silence_seconds, min_duration_seconds = self._listening_params(session)
        listen_kwargs = {
            'on_utterance': on_utterance,
            'loop': loop,
            'silence_seconds': silence_seconds,
            'min_duration_seconds': min_duration_seconds,
        }
        listen_kwargs.update(self._conversation_listen_kwargs(session))
        result = self.voice_transport.start_listening_sync(session.account_name, str(session.channel_id), **listen_kwargs)
        return self._log_start_result(session, result)

    async def start_async(self, session, *, loop=None) -> dict:
        if not self.should_listen(session):
            return {'status': 'skipped', 'reason': 'listen_disabled'}
        _key, on_utterance = self._bind_session(session, loop=loop)
        silence_seconds, min_duration_seconds = self._listening_params(session)
        if loop is None:
            loop = asyncio.get_running_loop()
        listen_kwargs = {
            'on_utterance': on_utterance,
            'loop': loop,
            'silence_seconds': silence_seconds,
            'min_duration_seconds': min_duration_seconds,
        }
        listen_kwargs.update(await self._conversation_listen_kwargs_async(session))
        result = await self.voice_transport.start_listening_async(session.account_name, str(session.channel_id), **listen_kwargs)
        return await self._log_start_result_async(session, result)

    def stop(self, account_name: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        session = self._sessions.pop(key, None)
        if session and self.conversation_runner:
            self.conversation_runner.stop(session.session_id)
        return self.voice_transport.stop_listening_sync(account_name, str(channel_id))

    async def stop_async(self, account_name: str, channel_id: str) -> dict:
        key = (account_name, str(channel_id))
        session = self._sessions.pop(key, None)
        if session and self.conversation_runner:
            self.conversation_runner.stop(session.session_id)
        return await self.voice_transport.stop_listening_async(account_name, str(channel_id))

    def _handle_utterance(
        self,
        account_name: str,
        channel_id: str,
        user_id: int,
        speaker_name: str,
        wav_bytes: bytes,
    ) -> None:
        session = self._sessions.get((account_name, channel_id))
        if not session:
            return
        if self._use_core_conversation(session):
            self._flush_merged_utterance(account_name, channel_id, user_id, speaker_name, wav_bytes)
            return
        loop = getattr(session, '_utterance_loop', None)
        if loop is None:
            self._flush_merged_utterance(account_name, channel_id, user_id, speaker_name, wav_bytes)
            return
        key = (account_name, channel_id, user_id)
        pending = self._merge_pending.get(key)
        if pending and pending.get('handle') and hasattr(pending['handle'], 'cancel'):
            pending['handle'].cancel()
        if pending:
            pending['wav_bytes'] = concat_wav_bytes(pending['wav_bytes'], wav_bytes)
            pending['speaker_name'] = speaker_name
        else:
            self._merge_pending[key] = {
                'wav_bytes': wav_bytes,
                'speaker_name': speaker_name,
                'handle': None,
            }
            pending = self._merge_pending[key]

        def _flush():
            state = self._merge_pending.pop(key, None)
            if not state:
                return
            VOICE_WORKER_POOL.submit(
                self._flush_merged_utterance,
                account_name,
                channel_id,
                user_id,
                state['speaker_name'],
                state['wav_bytes'],
            )

        pending['handle'] = loop.call_later(_UTTERANCE_MERGE_SECONDS, _flush)

    def _flush_merged_utterance(
        self,
        account_name: str,
        channel_id: str,
        user_id: int,
        speaker_name: str,
        wav_bytes: bytes,
    ) -> None:
        session = self._sessions.get((account_name, channel_id))
        if not session:
            return
        logger.info(
            'Voice utterance from %s in %s:%s (%s bytes)',
            speaker_name,
            account_name,
            channel_id,
            len(wav_bytes or b''),
        )
        try:
            if self._use_core_conversation(session) and self.conversation_runner:
                self.conversation_runner.interrupt_active_turn(session.session_id)
            else:
                self.voice_transport.stop_playback_sync(account_name, channel_id)
        except Exception:
            logger.debug('Barge-in playback stop failed for %s:%s', account_name, channel_id, exc_info=True)
        result = self.voice_perception_service.process_audio(
            session.session_id,
            audio_bytes=wav_bytes,
            speaker_id=str(user_id),
            speaker_name=speaker_name,
            guild_id=session.guild_id,
        )
        status = result.get('status', '')
        if status == 'transcribed':
            logger.info(
                'Voice transcript %s:%s from %s: %r',
                account_name,
                channel_id,
                speaker_name,
                str(result.get('text') or '')[:200],
            )
        elif status not in ('missing_session',):
            logger.info(
                'Voice perception %s for %s:%s from %s',
                status,
                account_name,
                channel_id,
                speaker_name,
            )
        if self._use_core_conversation(session):
            text = str(result.get('text') or '').strip()
            if status == 'transcribed' and text:
                turn = self._submit_conversation_turn(session, text, speaker_name=speaker_name)
                if turn.get('status') == 'submitted':
                    return
                if turn.get('status') not in ('filtered', 'skipped', 'runner_unavailable'):
                    logger.info('Discord conversation utterance bridge: %s', turn)
            return
        if self.voice_conversation_service:
            core_active = bool(
                self.conversation_runner and self.conversation_runner.is_active(session.session_id)
            )
            if not core_active:
                try:
                    convo = self.voice_conversation_service.handle_transcript(session, result)
                    if convo.get('status') not in ('replied', 'skipped'):
                        logger.info('Voice conversation result: %s', convo)
                    elif convo.get('status') == 'skipped' and convo.get('reason') not in ('empty', 'no_transcript'):
                        logger.info('Voice conversation skipped: %s', convo.get('reason'))
                except Exception:
                    logger.exception('Voice conversation handling failed for %s:%s', account_name, channel_id)
