"""Plugin-local conversation session manager (no core file edits).

Builds ConversationDriver + SpeechGate + DiscordConversationSource directly,
mirroring ConversationManager.start_external without modifying core.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import wave
from typing import Callable

from plugins.discord.sapphire.voice_chat import ensure_voice_chat, touch_voice_chat
from plugins.discord.voice.discord_conversation_source import DiscordConversationSource
from plugins.discord.voice.voice_addressing import is_stop_command, mentions_bot, resolve_bot_names, should_address_bot

logger = logging.getLogger(__name__)


def _get_system():
    from core.api_fastapi import get_system
    return get_system()


class DiscordConversationRunner:
    def __init__(
        self,
        *,
        playback_service=None,
        transport=None,
        settings_store=None,
        voice_session_service=None,
        speech_bridge=None,
        voice_transport=None,
    ):
        self.playback_service = playback_service
        self.transport = transport
        self.settings_store = settings_store
        self.voice_session_service = voice_session_service
        self.speech_bridge = speech_bridge
        self.voice_transport = voice_transport
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def active_chat_names(self) -> list[str]:
        """Chats with a live conversation session — the reaper skips these."""
        with self._lock:
            return [str(rec.get('chat_name') or '') for rec in self._sessions.values() if rec.get('chat_name')]

    def frame_feed_for(self, session_id: str):
        with self._lock:
            rec = self._sessions.get(session_id)
            return rec.get('frame_feed') if rec else None

    def start(self, session) -> dict:
        prepared = self._prepare_session(session)
        if prepared.get('status') != 'prepared':
            return prepared
        source = prepared['source']
        chat_name = prepared['chat_name']
        source.start()
        self._mark_started(session, chat_name)
        return {'status': 'active', 'chat_name': chat_name}

    async def start_async(self, session) -> dict:
        prepared = self._prepare_session(session)
        if prepared.get('status') != 'prepared':
            return prepared
        source = prepared['source']
        chat_name = prepared['chat_name']
        playback = await source.start_playback_async()
        if playback.get('status') == 'error':
            logger.warning(
                'Discord streaming playback start failed for %s:%s: %s',
                session.account_name,
                session.channel_id,
                playback.get('error'),
            )
            return {'status': 'error', 'error': playback.get('error', 'playback_failed')}
        source.start(start_playback=False)
        self._mark_started(session, chat_name)
        return {'status': 'active', 'chat_name': chat_name}

    def _mark_started(self, session, chat_name: str) -> None:
        if self.voice_session_service:
            self.voice_session_service.set_health(session.session_id, 'conversational')
        logger.info(
            'Discord conversation runner started session=%s chat=%s',
            session.session_id,
            chat_name,
        )

    def _prepare_session(self, session) -> dict:
        if not self.playback_service:
            return {'status': 'error', 'error': 'playback_unavailable'}
        try:
            system = _get_system()
        except ImportError:
            return {'status': 'error', 'error': 'system_unavailable'}
        if not system:
            return {'status': 'error', 'error': 'system_unavailable'}

        settings = (
            self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
            if self.settings_store
            else None
        )
        if settings and not getattr(settings.voice, 'conversation_core_enabled', True):
            return {'status': 'skipped', 'reason': 'conversation_core_disabled'}

        bot_names = resolve_bot_names(
            settings=settings,
            transport=self.transport,
            account_name=session.account_name,
        )
        chat_name = ensure_voice_chat(
            system,
            session.guild_id,
            session.channel_id,
            bot_names=bot_names,
            conversation_prompt_template=str(
                getattr(settings.voice, 'conversation_prompt_template', '') or ''
            )
            if settings
            else '',
            llm_provider=str(getattr(settings.voice, 'llm_provider', '') or '') if settings else '',
            llm_model=str(getattr(settings.voice, 'llm_model', '') or '') if settings else '',
            keep_history=bool(getattr(settings.voice, 'keep_chat_history', False)) if settings else False,
        )
        driver, gate, source, frame_feed = self._build_stack(
            system,
            session=session,
            chat_name=chat_name,
            bot_names=bot_names,
            settings=settings,
        )
        transcribe_fn = driver._transcribe_fn
        if settings is None or getattr(settings.voice, 'turn_cues_enabled', True):
            # Turn cues (v2.9 soundscape): think-pulse 1/s during STT+LLM dead
            # air, ding on barge-in, glitch sound on turn failure — same palette
            # as the phone surface.
            try:
                driver.set_cues(source.play_cue)
            except Exception as exc:
                logger.warning('Voice cue wiring failed: %s', exc)
        try:
            # One-shot silent regen if the first LLM token stalls (triple
            # think-tick instead of dead air, per the phone surface). Pairs
            # with the llm_request_timeout ensure_voice_chat stamps on the chat.
            from plugins.discord.sapphire.voice_chat import VOICE_LLM_TIMEOUT_SECONDS
            driver.set_llm_timeout(VOICE_LLM_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.debug('Voice llm timeout wiring failed: %s', exc)

        with self._lock:
            if session.session_id in self._sessions:
                return {'status': 'already_active', 'chat_name': chat_name}
            cap = int(getattr(settings.voice, 'max_conversation_sessions', 2) if settings else 2)
            if len(self._sessions) >= cap:
                return {'status': 'error', 'error': 'conversation_slot_cap'}
            self._sessions[session.session_id] = {
                'driver': driver,
                'gate': gate,
                'source': source,
                'frame_feed': frame_feed,
                'chat_name': chat_name,
                'bot_names': bot_names,
                'addressing_mode': str(
                    getattr(settings.voice, 'addressing_mode', 'bot_name') if settings else 'bot_name'
                ),
                'transcribe_fn': transcribe_fn,
                'guild_id': str(session.guild_id or ''),
                'channel_id': str(session.channel_id or ''),
                'last_addressee': '',
                'last_reply_end': 0.0,
            }
        return {'status': 'prepared', 'source': source, 'chat_name': chat_name}

    def stop(self, session_id: str) -> dict:
        with self._lock:
            rec = self._sessions.pop(session_id, None)
        if not rec:
            return {'status': 'not_active'}
        try:
            rec['source'].close()
        except Exception as exc:
            logger.warning('Discord conversation source close failed: %s', exc)
        try:
            rec['driver'].reset()
        except Exception as exc:
            logger.warning('Discord conversation driver reset failed: %s', exc)
        try:
            # Session over: the ephemeral TTL counts from now (M8).
            touch_voice_chat(_get_system(), rec.get('chat_name') or '')
        except Exception as exc:
            logger.debug('Voice chat touch failed: %s', exc)
        if self.voice_session_service:
            self.voice_session_service.set_health(session_id, 'connected')
        return {'status': 'stopped'}

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions)
        for session_id in ids:
            self.stop(session_id)

    def ensure_started(self, session) -> dict:
        if self.is_active(session.session_id):
            return {'status': 'already_active'}
        return self.start(session)

    async def ensure_started_async(self, session) -> dict:
        if self.is_active(session.session_id):
            return {'status': 'already_active'}
        return await self.start_async(session)

    def is_turn_active(self, session_id: str) -> bool:
        with self._lock:
            rec = self._sessions.get(session_id)
        if not rec:
            return False
        driver = rec['driver']
        source = rec['source']
        if getattr(driver, '_active_sink', None) is not None:
            return True
        if getattr(source, '_playing', False):
            return True
        try:
            from core.conversation.engine import RESPONDING
            if getattr(driver.engine, 'state', None) == RESPONDING:
                return True
        except Exception:
            pass
        return False

    def _note_reply_end(self, session_id: str) -> None:
        with self._lock:
            rec = self._sessions.get(session_id)
            if rec:
                rec['last_reply_end'] = time.monotonic()

    def _voice_settings(self, rec):
        """Live per-channel voice settings (a tuned window applies without rejoining)."""
        if not self.settings_store:
            return None
        try:
            return self.settings_store.resolve(guild_id=rec.get('guild_id'), channel_id=rec.get('channel_id')).voice
        except Exception:
            return None

    def _address_reason(self, session_id: str, rec: dict, raw: str, *, bot_names, addressing_mode,
                        speaker_id: str, humans, voice) -> str:
        """Why this utterance counts as addressed to her ('' = it doesn't).

        Mic test 2026-09-13 (Krem): saying her name in a VC trips his local
        wakeword, and "Name required each time" made every follow-up silent.
        The natural rule is per PERSON, not per channel — a human in a group
        answers when named, or when the person they just answered keeps
        talking to them. One human alone with her: everything is for her.
        Unknown occupancy fails closed (name required).
        """
        if addressing_mode == 'always':
            return 'always'
        if should_address_bot(raw, bot_names, addressing_mode=addressing_mode):
            return 'named'
        solo = bool(getattr(voice, 'solo_no_name', True)) if voice is not None else True
        if solo and humans == 1:
            return 'solo'
        if speaker_id and rec.get('last_addressee') == speaker_id:
            if self.is_turn_active(session_id):
                return 'follow_up'          # interjecting on her reply to you
            try:
                follow = float(getattr(voice, 'follow_up_seconds', 20.0) if voice is not None else 20.0)
            except (TypeError, ValueError):
                follow = 20.0
            end = float(rec.get('last_reply_end') or 0.0)
            if follow > 0 and end and (time.monotonic() - end) <= follow:
                return 'follow_up'
        return ''

    def submit_turn_text(self, session_id: str, text: str, *, speaker_id: str = '', humans=None) -> dict:
        raw = str(text or '').strip()
        if not raw:
            return {'status': 'empty'}
        with self._lock:
            rec = self._sessions.get(session_id)
        if not rec:
            return {'status': 'not_active'}
        voice = self._voice_settings(rec)
        live_mode = getattr(voice, 'addressing_mode', None) if voice is not None else None
        addressing_mode = str(live_mode if isinstance(live_mode, str) and live_mode else rec.get('addressing_mode') or 'bot_name')
        bot_names = list(rec.get('bot_names') or [])
        speaker_id = str(speaker_id or '')
        if is_stop_command(raw):
            if (
                addressing_mode == 'always'
                or mentions_bot(raw, bot_names)
                or self.is_turn_active(session_id)
            ):
                self.interrupt_active_turn(session_id)
                logger.info('[DISCORD] stop command — halted without new turn (%d chars)', len(raw))
                logger.debug('[DISCORD] stop command text: %r', raw[:120])
                return {'status': 'stopped'}
        reason = self._address_reason(
            session_id, rec, raw,
            bot_names=bot_names, addressing_mode=addressing_mode,
            speaker_id=speaker_id, humans=humans, voice=voice,
        )
        if not reason:
            logger.info(
                '[DISCORD] utterance bridge skipped undirected speech (%d chars, mode=%s, %d bot names, humans=%s)',
                len(raw), addressing_mode, len(bot_names), humans,
            )
            logger.debug('[DISCORD] undirected speech text: %r', raw[:120])
            return {'status': 'filtered'}
        # Only an ADDRESSED utterance replaces her turn. In a group, two people
        # talking to each other while she answers a third must not cut her off.
        self.interrupt_active_turn(session_id)
        with self._lock:
            rec['last_addressee'] = speaker_id
        driver = rec['driver']
        driver._discord_pending_text = raw
        logger.info('[DISCORD] utterance bridge submitting turn (%d chars, %s, humans=%s)', len(raw), reason, humans)
        logger.debug('[DISCORD] utterance bridge turn text: %r', raw[:200])
        self._signal_thinking(rec)
        driver._spawn(driver._run_turn, b'\x00\x00' * 16000)
        return {'status': 'submitted', 'reason': reason}

    def submit_turn_pcm(self, session_id: str, pcm: bytes) -> dict:
        if not pcm:
            return {'status': 'empty'}
        with self._lock:
            rec = self._sessions.get(session_id)
        if not rec:
            return {'status': 'not_active'}
        driver = rec['driver']
        logger.info('[DISCORD] utterance bridge submitting pcm turn (%s bytes)', len(pcm))
        self._signal_thinking(rec)
        driver._spawn(driver._run_turn, pcm)
        return {'status': 'submitted'}

    def _signal_thinking(self, rec) -> None:
        """Typing indicator in the voice channel's text chat while the turn runs.

        Discord clears it after ~10s or on message send; the LLM+TTS window is
        shorter, so a single trigger covers the whole 'is she working?' gap.
        """
        source = rec.get('source')
        if not self.transport or source is None:
            return
        try:
            self.transport.trigger_typing_sync(
                source.channel_id,
                account_name=source.account_name,
            )
        except Exception as exc:
            logger.debug('Voice thinking indicator failed: %s', exc)

    def interrupt_active_turn(self, session_id: str) -> bool:
        if not self.is_turn_active(session_id):
            return False
        with self._lock:
            rec = self._sessions.get(session_id)
        if not rec:
            return False
        driver = rec['driver']
        source = rec['source']
        try:
            driver.system.cancel_generation(chat_name=driver._chat_name)
        except Exception as exc:
            logger.debug('Discord conversation cancel failed: %s', exc)
        try:
            source.interrupt_playback()
        except Exception as exc:
            logger.debug('Discord conversation playback interrupt failed: %s', exc)
        try:
            driver.engine.turn_finished()
        except Exception as exc:
            logger.debug('Discord conversation turn_finished failed: %s', exc)
        driver._active_sink = None
        logger.info('[DISCORD] interrupted active conversation turn session=%s', session_id)
        return True

    @staticmethod
    def _barge_hold_ms(settings) -> int:
        """Discord's own hold window (voice.barge_hold_ms), clamped to sane bounds."""
        try:
            value = int(getattr(settings.voice, 'barge_hold_ms', 250)) if settings is not None else 250
        except (TypeError, ValueError, AttributeError):
            value = 250
        return max(30, min(2000, value))

    def _build_stack(self, system, *, session, chat_name: str, bot_names: list[str], settings):
        import config
        from core.conversation.driver import ConversationDriver
        from core.conversation.vad import SpeechGate
        from plugins.discord.voice.discord_frame_feed import DiscordFrameFeed

        driver = ConversationDriver(
            system,
            chat_name=chat_name,
            start_word='',
            transcribe_fn=lambda _pcm: None,
            start_word_fuzzy=float(getattr(config, 'CONVERSATION_START_WORD_FUZZY', 0.7)),
            endpoint_silence_ms=int(getattr(config, 'CONVERSATION_ENDPOINT_SILENCE_MS', 700)),
            min_speech_ms=int(getattr(config, 'CONVERSATION_MIN_SPEECH_MS', 200)),
            barge_hold_ms=self._barge_hold_ms(settings),
        )
        driver._transcribe_fn = self._build_transcribe_fn(
            system,
            driver=driver,
            settings=settings,
            bot_names=bot_names,
        )
        gate = SpeechGate(threshold=float(getattr(config, 'CONVERSATION_VAD_THRESHOLD', 0.5)))
        source = DiscordConversationSource(
            driver,
            gate,
            self.playback_service,
            account_name=session.account_name,
            channel_id=str(session.channel_id),
            speech_bridge=self.speech_bridge,
            voice_transport=self.voice_transport,
            on_reply_end=lambda: self._note_reply_end(session.session_id),
        )
        driver.set_sink(source)
        frame_feed = DiscordFrameFeed(source.push_pcm)
        original_run_turn = driver._run_turn

        def _run_turn_with_responding_state(pcm):
            from core.conversation.engine import RESPONDING
            driver.engine.state = RESPONDING
            driver.engine.barge_enabled = False
            driver.engine._barge_ms = 0.0
            return original_run_turn(pcm)

        driver._run_turn = _run_turn_with_responding_state
        return driver, gate, source, frame_feed

    def _build_transcribe_fn(self, system, *, driver, settings, bot_names: list[str]) -> Callable:
        sample_rate = 16000
        addressing_mode = 'bot_name'
        if settings is not None:
            addressing_mode = str(getattr(settings.voice, 'addressing_mode', 'bot_name') or 'bot_name')

        def transcribe(pcm: bytes) -> str | None:
            pending = getattr(driver, '_discord_pending_text', None)
            from_bridge = pending is not None
            if from_bridge:
                driver._discord_pending_text = None
                text = str(pending).strip()
            else:
                wc = getattr(system, 'whisper_client', None)
                if wc is None:
                    return None
                fd, path = tempfile.mkstemp(suffix='.wav')
                os.close(fd)
                try:
                    with wave.open(path, 'wb') as handle:
                        handle.setnchannels(1)
                        handle.setsampwidth(2)
                        handle.setframerate(sample_rate)
                        handle.writeframes(pcm)
                    text = str(wc.transcribe_file(path) or '').strip()
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            if not text:
                return None
            logger.info('[DISCORD] conversation turn transcribed (%d chars)', len(text))
            logger.debug('[DISCORD] conversation turn text: %r', text[:200])
            if from_bridge:
                # submit_turn_text already ruled this utterance addressed (named,
                # solo, follow-up). Re-running the bare name check here silently
                # killed every solo/follow-up turn after "typing…" (mic test 1b).
                return text
            if addressing_mode == 'bot_name' and not should_address_bot(text, bot_names, addressing_mode=addressing_mode):
                logger.info('[DISCORD] addressing filter skipped undirected speech (%d chars)', len(text))
                logger.debug('[DISCORD] undirected speech text: %r', text[:120])
                return ''
            return text

        return transcribe
