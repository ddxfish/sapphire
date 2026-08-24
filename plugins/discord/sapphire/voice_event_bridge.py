"""Subscribe to core voice/TTS events for Discord conversational sessions."""

from __future__ import annotations

import logging
import threading

from plugins.discord.sapphire.voice_chat import is_voice_chat_name, parse_voice_chat_name, voice_chat_name

logger = logging.getLogger(__name__)


class VoiceEventBridge:
    def __init__(
        self,
        *,
        voice_session_repository=None,
        trace_repository=None,
        world_model_service=None,
        conversation_runner=None,
    ):
        self.voice_session_repository = voice_session_repository
        self.trace_repository = trace_repository
        self.world_model_service = world_model_service
        self.conversation_runner = conversation_runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name='discord-voice-events')
        self._thread.start()
        logger.info('[DISCORD] voice event bridge started')

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _listen_loop(self) -> None:
        try:
            from core.event_bus import Events, get_event_bus
        except ImportError:
            logger.warning('[DISCORD] voice event bridge unavailable — no event_bus')
            return
        bus = get_event_bus()
        gen = bus.subscribe(replay=False)
        try:
            for event in gen:
                if self._stop.is_set():
                    break
                self._handle(event, Events)
        except GeneratorExit:
            pass
        finally:
            try:
                gen.close()
            except Exception:
                pass

    def _handle(self, event: dict, events) -> None:
        event_type = str(event.get('type') or '')
        data = event.get('data') if isinstance(event.get('data'), dict) else {}
        chat = str(data.get('chat') or '')
        surface = str(data.get('surface') or '')

        if event_type in (
            events.VOICE_TURN_START,
            events.VOICE_TURN_CHUNK,
            events.VOICE_TURN_END,
        ):
            if not is_voice_chat_name(chat):
                return
            self._handle_voice_turn(event_type, chat, data)
            return

        if event_type in (events.TTS_PLAYING, events.TTS_STOPPED) and surface == 'discord':
            self._trace('voice_tts_state', event_type, data)

    def _handle_voice_turn(self, event_type: str, chat_name: str, data: dict) -> None:
        session = self._session_for_chat(chat_name)
        payload = {
            'chat': chat_name,
            'event': event_type,
            'message_id': data.get('message_id'),
            'session_id': getattr(session, 'session_id', None) if session else None,
        }
        if event_type.endswith('start'):
            payload['user_text'] = str(data.get('user_text') or '')[:500]
            self._trace('voice_conversation_turn', 'Conversation turn started', payload)
            if session and payload.get('user_text') and self.world_model_service:
                self.world_model_service._record_observation('voice_conversation_turn', session.channel_id, {
                    'session_id': session.session_id,
                    'text': payload['user_text'],
                    'chat': chat_name,
                })
        elif event_type.endswith('chunk'):
            text = str(data.get('text') or '')
            if text:
                payload['text_preview'] = text[:160]
                self._trace('voice_conversation_chunk', 'Conversation reply chunk', payload)
        elif event_type.endswith('end'):
            self._trace('voice_conversation_turn_end', 'Conversation turn finished', payload)

    def _session_for_chat(self, chat_name: str):
        if not self.voice_session_repository:
            return None
        parsed = parse_voice_chat_name(chat_name)
        if not parsed:
            return None
        guild_id, channel_id = parsed
        finder = getattr(self.voice_session_repository, 'get_active_by_guild_channel', None)
        if callable(finder):
            return finder(guild_id, channel_id)
        return None

    def _trace(self, trace_type: str, message: str, payload: dict) -> None:
        if not self.trace_repository:
            return
        try:
            self.trace_repository.record_trace(trace_type, message, payload)
        except Exception as exc:
            logger.debug('Voice event trace failed: %s', exc)

    def diagnostics(self) -> dict:
        runner = self.conversation_runner
        active = []
        if runner is not None:
            with runner._lock:
                for session_id, rec in runner._sessions.items():
                    active.append({
                        'session_id': session_id,
                        'chat_name': rec.get('chat_name'),
                    })
        return {
            'bridge_running': bool(self._thread and self._thread.is_alive()),
            'active_conversations': active,
            'active_count': len(active),
        }
