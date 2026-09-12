"""Speech-to-text perception and voice observations."""

from __future__ import annotations

import time

from plugins.discord.models.observations import VoiceTranscriptObservation


class VoicePerceptionService:
    def __init__(self, *, voice_session_repository, speech_bridge=None, world_model_service=None, trace_repository=None):
        self.voice_session_repository = voice_session_repository
        self.speech_bridge = speech_bridge
        self.world_model_service = world_model_service
        self.trace_repository = trace_repository

    def process_audio(
        self,
        session_id: str,
        *,
        audio_bytes: bytes,
        speaker_id: str = '',
        speaker_name: str = '',
        guild_id: str = '',
        guild_name: str = '',
        channel_name: str = 'voice',
    ) -> dict:
        session = self.voice_session_repository.get_session(session_id)
        if not session:
            return {'status': 'missing_session'}
        transcript = self._transcribe(audio_bytes, speaker_hint=speaker_name or speaker_id)
        text = (transcript.get('text') or '').strip()
        confidence = float(transcript.get('confidence', 0.5))
        if not text:
            return {'status': 'empty'}
        segment_id = self.voice_session_repository.add_transcript(
            session_id,
            session.account_name,
            session.channel_id,
            text,
            speaker_id=speaker_id,
            speaker_name=speaker_name or transcript.get('speaker', ''),
            confidence=confidence,
        )
        now = time.time()
        observation = VoiceTranscriptObservation(
            observation_id=f'voice:{session_id}:{segment_id}',
            account_name=session.account_name,
            guild_id=guild_id or session.guild_id,
            guild_name=guild_name or 'Voice',
            channel_id=session.channel_id,
            channel_name=channel_name,
            author_id=speaker_id,
            username=speaker_name,
            display_name=speaker_name,
            created_at=now,
            is_dm=False,
            session_id=session_id,
            text=text,
            confidence=confidence,
            transcript_segment_id=segment_id,
        )
        if self.world_model_service:
            self.world_model_service._record_observation('voice_transcript', session.channel_id, {
                'session_id': session_id,
                'segment_id': segment_id,
                'speaker_id': speaker_id,
                'text': text,
                'confidence': confidence,
            })
        if self.trace_repository:
            self.trace_repository.record_trace('voice_transcript', 'Transcribed voice segment', {
                'session_id': session_id,
                'segment_id': segment_id,
                'confidence': confidence,
            })
        return {'status': 'transcribed', 'text': text, 'confidence': confidence, 'observation': observation}

    def _transcribe(self, audio_bytes: bytes, *, speaker_hint: str = '') -> dict:
        if self.speech_bridge and hasattr(self.speech_bridge, 'transcribe_audio'):
            return self.speech_bridge.transcribe_audio(audio_bytes, speaker_hint=speaker_hint)
        return {'text': '', 'confidence': 0.0}
