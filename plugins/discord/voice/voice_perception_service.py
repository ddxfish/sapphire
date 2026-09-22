"""Speech-to-text perception and voice observations."""

from __future__ import annotations

import time
import uuid

from plugins.discord.models.observations import VoiceTranscriptObservation
from plugins.discord.models.voice import VoiceMode

# Modes whose whole point is the archive: they persist regardless of the toggle.
_ARCHIVING_MODES = {VoiceMode.TRANSCRIBE_ONLY.value, VoiceMode.SUMMARIZE_ONLY.value}


class VoicePerceptionService:
    def __init__(
        self,
        *,
        voice_session_repository,
        speech_bridge=None,
        trace_repository=None,
        settings_store=None,
    ):
        self.voice_session_repository = voice_session_repository
        self.speech_bridge = speech_bridge
        self.trace_repository = trace_repository
        self.settings_store = settings_store

    def _archives(self, session) -> bool:
        """Voice.md: Transcription is OFF by default and means it (M7, hunt
        2026-09-12 — every utterance used to land in voice_transcripts no matter
        the toggle). Conversational voice still HEARS the text (it rides the
        return value into the turn); it just keeps no row. Fail closed: no
        settings store or a resolve error = no archive."""
        mode = session.mode.value if isinstance(session.mode, VoiceMode) else str(session.mode or '')
        if mode in _ARCHIVING_MODES:
            return True
        if self.settings_store is None:
            return False
        try:
            settings = self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
        except Exception:
            return False
        return bool(settings and settings.voice.transcription_enabled)

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
        persisted = self._archives(session)
        segment_id = 0
        if persisted:
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
            observation_id=f'voice:{session_id}:{segment_id or uuid.uuid4().hex[:8]}',
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
        if self.trace_repository:
            self.trace_repository.record_trace('voice_transcript', 'Transcribed voice segment', {
                'session_id': session_id,
                'segment_id': segment_id,
                'confidence': confidence,
                'persisted': persisted,
            })
        return {
            'status': 'transcribed',
            'text': text,
            'confidence': confidence,
            'observation': observation,
            'persisted': persisted,
        }

    def _transcribe(self, audio_bytes: bytes, *, speaker_hint: str = '') -> dict:
        if self.speech_bridge and hasattr(self.speech_bridge, 'transcribe_audio'):
            return self.speech_bridge.transcribe_audio(audio_bytes, speaker_hint=speaker_hint)
        return {'text': '', 'confidence': 0.0}
