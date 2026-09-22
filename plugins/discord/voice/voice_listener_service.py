"""Start/stop Discord voice recording and route utterances into the conversation runner (S6).

One lane: every utterance the sink hands over is transcribed (speech bridge →
Whisper) on a worker thread, offered to add-ons (discord_voice_utterance) and
submitted to core's conversation engine through the runner, whose addressing
gate decides whether it is for her. Nothing is archived.
"""

from __future__ import annotations

import asyncio
import logging

from plugins.discord import hooks_out
from plugins.discord.sapphire.voice_prompt import format_voice_turn_text
from plugins.discord.voice import voice_workers

logger = logging.getLogger(__name__)
_MIN_UTTERANCE_SECONDS = 0.35


class VoiceListenerService:
    def __init__(self, *, voice_transport, conversation_runner=None, speech_bridge=None, settings_store=None,
                 trace_repository=None):
        self.voice_transport = voice_transport
        self.conversation_runner = conversation_runner
        self.speech_bridge = speech_bridge
        self.settings_store = settings_store
        self.trace_repository = trace_repository
        self._sessions = {}

    def _listening_params(self) -> tuple[float, float]:
        silence_seconds = 2.5
        if self.settings_store:
            settings = self.settings_store.resolve()
            if settings:
                silence_seconds = max(1.8, float(getattr(settings.voice, 'min_silence_seconds', 1.0)) + 1.2)
        return silence_seconds, _MIN_UTTERANCE_SECONDS

    def _runner_ok(self, result: dict) -> bool:
        return result.get('status') in ('active', 'already_active')

    def _frame_feed_listen_kwargs(self, session) -> dict:
        if not self.conversation_runner:
            return {}
        frame_feed = self.conversation_runner.frame_feed_for(session.session_id)
        if not frame_feed:
            return {}
        session_id = session.session_id

        def on_pcm_frame(user_id, pcm_stereo, rms, is_speech=None):
            """Barge-in the core way (mic test 2026-09-13): while a turn is live
            every accepted frame rides into the conversation engine with NO
            speech hint — core's Silero gate, arming and hold window decide the
            barge, the driver's own barge callback cancels the LLM and cuts
            audio. push_stereo_pcm is a resample + queue put: safe on py-cord's
            router thread."""
            del user_id, rms, is_speech
            runner = self.conversation_runner
            if not runner or not runner.is_turn_active(session_id):
                return
            try:
                frame_feed.push_stereo_pcm(pcm_stereo)
            except Exception:
                logger.debug('Voice frame feed push failed', exc_info=True)

        return {'on_pcm_frame': on_pcm_frame}

    def _conversation_listen_kwargs(self, session) -> dict:
        if not self.conversation_runner:
            return {}
        result = self.conversation_runner.start(session)
        if not self._runner_ok(result):
            logger.warning('Discord conversation runner not started for %s:%s: %s', session.account_name, session.channel_id, result)
            return {}
        return self._frame_feed_listen_kwargs(session)

    async def _conversation_listen_kwargs_async(self, session) -> dict:
        if not self.conversation_runner:
            return {}
        result = await self.conversation_runner.start_async(session)
        if not self._runner_ok(result):
            logger.warning('Discord conversation runner not started for %s:%s: %s', session.account_name, session.channel_id, result)
            return {}
        return self._frame_feed_listen_kwargs(session)

    def _ensure_runner(self, session) -> bool:
        if not self.conversation_runner:
            return False
        if self.conversation_runner.is_active(session.session_id):
            return True
        result = self.conversation_runner.ensure_started(session)
        if not self._runner_ok(result):
            logger.warning('Discord conversation runner unavailable for %s:%s: %s', session.account_name, session.channel_id, result)
            return False
        return True

    async def _ensure_runner_async(self, session) -> None:
        if not self.conversation_runner or self.conversation_runner.is_active(session.session_id):
            return
        result = await self.conversation_runner.start_async(session)
        if not self._runner_ok(result):
            logger.warning('Discord conversation runner recovery failed for %s:%s: %s', session.account_name, session.channel_id, result)

    def _submit_conversation_turn(self, session, text: str, *, speaker_name: str = '', speaker_id: str = '') -> dict:
        if not text or not self.conversation_runner:
            return {'status': 'skipped'}
        if not self._ensure_runner(session):
            return {'status': 'runner_unavailable'}
        # The runner interrupts her only once the utterance counts as addressed
        # (mic test 2026-09-13) — an unconditional interrupt here let any
        # bystander's chatter cut her off in a group.
        return self.conversation_runner.submit_turn_text(
            session.session_id, format_voice_turn_text(text, speaker_name=speaker_name),
            speaker_id=str(speaker_id or ''), humans=self._human_count(session),
        )

    def _human_count(self, session):
        """Live humans in the channel (None = unknown → the name rule applies)."""
        transport = getattr(self.voice_transport, 'discord_transport', None)
        getter = getattr(transport, 'get_voice_channel_state_sync', None)
        if not callable(getter):
            return None
        try:
            state = getter(session.account_name, str(session.channel_id)) or {}
        except Exception:
            return None
        humans = state.get('human_count') if isinstance(state, dict) else None
        return int(humans) if isinstance(humans, (int, float)) else None

    def _bind_session(self, session, *, loop=None):
        self._sessions[(session.account_name, str(session.channel_id))] = session

        def on_utterance(user_id, speaker_name, wav_bytes):
            voice_workers.submit(self._handle_utterance, session.account_name, str(session.channel_id),
                                 user_id, speaker_name, wav_bytes)

        return on_utterance

    def _log_start_result(self, session, result: dict) -> dict:
        status = result.get('status', '')
        if status == 'listening':
            logger.info('Voice listener started for %s:%s', session.account_name, session.channel_id)
        elif status == 'already_listening':
            logger.debug('Voice listener already active for %s:%s', session.account_name, session.channel_id)
        else:
            logger.warning('Voice listener not started for %s:%s: %s', session.account_name, session.channel_id, result)
        return result

    def start(self, session, *, loop=None) -> dict:
        on_utterance = self._bind_session(session, loop=loop)
        silence_seconds, min_duration_seconds = self._listening_params()
        kwargs = {'on_utterance': on_utterance, 'loop': loop, 'silence_seconds': silence_seconds,
                  'min_duration_seconds': min_duration_seconds}
        kwargs.update(self._conversation_listen_kwargs(session))
        result = self.voice_transport.start_listening_sync(session.account_name, str(session.channel_id), **kwargs)
        if result.get('status') in ('listening', 'already_listening'):
            self._ensure_runner(session)
        return self._log_start_result(session, result)

    async def start_async(self, session, *, loop=None) -> dict:
        on_utterance = self._bind_session(session, loop=loop)
        silence_seconds, min_duration_seconds = self._listening_params()
        if loop is None:
            loop = asyncio.get_running_loop()
        kwargs = {'on_utterance': on_utterance, 'loop': loop, 'silence_seconds': silence_seconds,
                  'min_duration_seconds': min_duration_seconds}
        kwargs.update(await self._conversation_listen_kwargs_async(session))
        result = await self.voice_transport.start_listening_async(session.account_name, str(session.channel_id), **kwargs)
        if result.get('status') in ('listening', 'already_listening'):
            await self._ensure_runner_async(session)
        return self._log_start_result(session, result)

    def stop(self, account_name: str, channel_id: str) -> dict:
        session = self._sessions.pop((account_name, str(channel_id)), None)
        if session and self.conversation_runner:
            self.conversation_runner.stop(session.session_id)
        return self.voice_transport.stop_listening_sync(account_name, str(channel_id))

    async def stop_async(self, account_name: str, channel_id: str) -> dict:
        session = self._sessions.pop((account_name, str(channel_id)), None)
        if session and self.conversation_runner:
            # runner.stop joins the source thread and makes a SYNC transport call
            # that raises on the daemon loop — off-loop it (M21).
            stop_async = getattr(self.conversation_runner, 'stop_async', None)
            if stop_async is not None:
                await stop_async(session.session_id)
            else:
                self.conversation_runner.stop(session.session_id)
        return await self.voice_transport.stop_listening_async(account_name, str(channel_id))

    def _transcribe(self, wav_bytes: bytes, *, speaker_hint: str = '') -> str:
        bridge = self.speech_bridge
        if bridge is None or not hasattr(bridge, 'transcribe_audio'):
            return ''
        try:
            result = bridge.transcribe_audio(wav_bytes, speaker_hint=speaker_hint) or {}
        except Exception:
            logger.exception('Voice transcription failed')
            return ''
        return str(result.get('text') or '').strip()

    def _handle_utterance(self, account_name: str, channel_id: str, user_id, speaker_name: str, wav_bytes: bytes) -> None:
        """Worker thread: STT → add-ons → the conversation runner's addressing gate."""
        session = self._sessions.get((account_name, channel_id))
        if not session:
            return
        logger.debug('Voice utterance from %s in %s:%s (%s bytes)', speaker_name, account_name, channel_id, len(wav_bytes or b''))
        text = self._transcribe(wav_bytes, speaker_hint=speaker_name or str(user_id))
        if not text:
            return
        if self.trace_repository:
            self.trace_repository.record_trace('voice_transcript', 'Transcribed voice segment',
                                               {'session_id': session.session_id, 'chars': len(text)})
        logger.debug('Voice transcript %s:%s from %s: %r', account_name, channel_id, speaker_name, text[:200])
        # S0 door: add-ons hear what was said (worker thread).
        hooks_out.fire('discord_voice_utterance', {
            'account': account_name, 'guild_id': str(getattr(session, 'guild_id', '') or ''),
            'channel_id': str(channel_id), 'speaker_id': str(user_id), 'speaker_name': speaker_name, 'text': text,
        })
        turn = self._submit_conversation_turn(session, text, speaker_name=speaker_name, speaker_id=str(user_id))
        if turn.get('status') not in ('submitted', 'filtered', 'skipped', 'runner_unavailable', 'stopped'):
            logger.info('Discord conversation utterance bridge: %s', turn)
