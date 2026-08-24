"""Text-to-speech voice output execution."""

from __future__ import annotations

from plugins.discord.models.intentions import SpeakVoiceIntention


class VoiceExecutionService:
    def __init__(
        self,
        *,
        speech_bridge=None,
        voice_transport=None,
        policy_service=None,
        turn_taking_service=None,
        trace_repository=None,
        trace_service=None,
        settings_store=None,
    ):
        self.speech_bridge = speech_bridge
        self.voice_transport = voice_transport
        self.policy_service = policy_service
        self.turn_taking_service = turn_taking_service
        self.trace_repository = trace_repository
        self.trace_service = trace_service
        self.settings_store = settings_store

    def execute(self, intention: SpeakVoiceIntention) -> dict:
        settings = self.settings_store.resolve(channel_id=intention.channel_id) if self.settings_store else None
        if self.policy_service and settings:
            decision = self.policy_service.evaluate_voice_speak(intention, settings)
            if not decision.get('allowed'):
                if self.trace_service:
                    self.trace_service.record_voice_decision('speak_blocked', {
                        'reason': decision.get('reason', 'denied'),
                        'channel_id': intention.channel_id,
                    })
                return {'status': 'blocked', 'reason': decision.get('reason', 'denied')}
        if self.turn_taking_service:
            if intention.reason == 'voice_conversation':
                allowed = self.turn_taking_service.may_reply_to_utterance(intention.channel_id)
            else:
                allowed = self.turn_taking_service.may_speak(intention.channel_id)
            if not allowed:
                if self.trace_service:
                    self.trace_service.record_voice_decision('speak_blocked', {
                        'reason': 'turn_taking',
                        'channel_id': intention.channel_id,
                    })
                return {'status': 'blocked', 'reason': 'turn_taking'}
        if not self.speech_bridge or not hasattr(self.speech_bridge, 'synthesize_speech'):
            return {'status': 'skipped', 'reason': 'speech_unavailable'}
        if not self.voice_transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        audio = self.speech_bridge.synthesize_speech(intention.text)
        audio_bytes = audio.get('audio_bytes') if isinstance(audio, dict) else audio
        if not audio_bytes:
            return {'status': 'skipped', 'reason': 'empty_audio'}
        result = self.voice_transport.play_audio_sync(
            intention.account_name,
            intention.channel_id,
            audio_bytes,
            reason=intention.reason,
        )
        if self.turn_taking_service:
            self.turn_taking_service.note_bot_spoke(intention.channel_id)
        if self.trace_repository:
            self.trace_repository.record_trace('voice_spoken', 'Played synthesized speech', {
                'channel_id': intention.channel_id,
                'reason': intention.reason,
                'text_preview': intention.text[:120],
                'transport_status': result.get('status'),
            })
        if isinstance(result, dict) and result.get('status') == 'error':
            return {'status': 'error', 'reason': result.get('error', 'playback_failed'), 'transport': result}
        if isinstance(result, dict) and result.get('status') not in {'playing', 'played', 'sent', None}:
            if result.get('status'):
                return {'status': 'error', 'reason': f"playback_{result.get('status')}", 'transport': result}
        return {'status': 'spoken', 'transport': result}
