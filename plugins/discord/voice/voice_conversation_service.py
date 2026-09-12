"""Conversational voice loop: transcript -> LLM -> TTS."""

from __future__ import annotations

import logging

from plugins.discord.models.intentions import SpeakVoiceIntention
from plugins.discord.models.voice import VoiceMode

logger = logging.getLogger(__name__)


class VoiceConversationService:
    def __init__(
        self,
        *,
        voice_execution_service,
        voice_session_repository,
        settings_store=None,
        reply_style_service=None,
        trace_repository=None,
    ):
        self.voice_execution_service = voice_execution_service
        self.voice_session_repository = voice_session_repository
        self.settings_store = settings_store
        self.reply_style_service = reply_style_service
        self.trace_repository = trace_repository

    def handle_transcript(self, session, perception_result: dict) -> dict:
        if perception_result.get('status') != 'transcribed':
            reason = perception_result.get('status', 'no_transcript')
            logger.debug('Voice conversation skipped: %s', reason)
            return {'status': 'skipped', 'reason': reason}
        settings = (
            self.settings_store.resolve(guild_id=session.guild_id, channel_id=session.channel_id)
            if self.settings_store
            else None
        )
        if settings and getattr(settings.voice, 'conversation_core_enabled', True):
            return {'status': 'skipped', 'reason': 'core_conversation_active'}
        mode = session.mode
        session_mode = mode.value if isinstance(mode, VoiceMode) else str(mode)
        settings_mode = str(settings.voice.mode) if settings else None
        conversational = (
            session_mode == VoiceMode.CONVERSATIONAL.value
            or settings_mode == VoiceMode.CONVERSATIONAL.value
        )
        if not conversational:
            logger.debug(
                'Voice conversation skipped: session_mode=%s settings_mode=%s',
                session_mode,
                settings_mode,
            )
            return {'status': 'skipped', 'reason': 'not_conversational'}
        if settings and (
            not settings.voice.enabled
            or settings.voice.emergency_disabled
            or not settings.voice.speaking_enabled
        ):
            logger.warning(
                'Voice conversation blocked for %s:%s — enable voice + speaking in plugin settings',
                session.account_name,
                session.channel_id,
            )
            return {'status': 'skipped', 'reason': 'speaking_disabled'}
        user_text = str(perception_result.get('text') or '').strip()
        if not user_text:
            return {'status': 'skipped', 'reason': 'empty'}
        logger.info('Voice conversation heard: %r', user_text[:200])
        prompt = self._build_prompt(session, user_text)
        reply = self._llm_reply(prompt, settings=settings)
        if not reply:
            return {'status': 'skipped', 'reason': 'empty_reply'}
        reply = self._extract_spoken_reply(reply)
        if not reply:
            return {'status': 'skipped', 'reason': 'empty_reply'}
        intention = SpeakVoiceIntention(
            intention_type='speak_voice',
            account_name=session.account_name,
            channel_id=session.channel_id,
            message_id='',
            reason='voice_conversation',
            text=reply,
        )
        result = self.voice_execution_service.execute(intention)
        logger.info(
            'Voice conversation reply status=%s preview=%r',
            result.get('status'),
            reply[:120],
        )
        if result.get('status') != 'spoken':
            return {
                'status': 'skipped',
                'reason': result.get('reason', result.get('status')),
                'execution': result,
            }
        if self.trace_repository:
            self.trace_repository.record_trace(
                'voice_conversation_reply',
                'Spoke conversational reply',
                {
                    'session_id': session.session_id,
                    'channel_id': session.channel_id,
                    'user_text_preview': user_text[:120],
                    'reply_preview': reply[:120],
                    'status': result.get('status'),
                },
            )
        return {'status': 'replied', 'execution': result, 'reply': reply}

    def _extract_spoken_reply(self, raw: str) -> str:
        text = str(raw or '').strip()
        if not text:
            return ''
        if self.reply_style_service:
            parsed = self.reply_style_service.parse_llm_output(text)
            text = ' '.join(chunk.strip() for chunk in parsed.chunks if chunk).strip()
        return text

    def _build_prompt(self, session, user_text: str) -> str:
        lines = []
        if self.voice_session_repository:
            recent = self.voice_session_repository.list_transcripts(session.session_id, limit=8)
            for row in recent[:-1]:
                speaker = row.get('speaker_name') or row.get('speaker_id') or 'speaker'
                lines.append(f"{speaker}: {row.get('text', '')}")
        history = '\n'.join(lines).strip()
        parts = [
            'You are in a live Discord voice channel. Reply briefly (1-3 sentences) for spoken delivery.',
            'Do not use markdown, bullet lists, or tool calls.',
        ]
        if history:
            parts.append(f'Recent voice transcript:\n{history}')
        parts.append(f'User just said: {user_text}')
        parts.append('Your spoken reply:')
        return '\n\n'.join(parts)

    def _llm_reply(self, user_input: str, *, settings=None) -> str:
        try:
            from core.api_fastapi import get_system
            from plugins.discord.sapphire.llm_settings import (
                cognitive_llm_from_settings,
                resolve_discord_llm_provider,
            )

            system = get_system()
        except Exception as exc:
            logger.warning('Voice conversation could not reach Sapphire system: %s', exc)
            return ''
        llm = getattr(system, 'llm_chat', None)
        if not llm:
            return ''
        primary, model = cognitive_llm_from_settings(settings) if settings else ('auto', '')
        try:
            provider_key, provider, gen_params = resolve_discord_llm_provider(
                system,
                primary,
                model,
            )
            if not provider:
                return ''
            max_tokens = gen_params.get('max_tokens') or 256
            gen_params = {**gen_params, 'max_tokens': min(int(max_tokens), 256)}
            messages = [
                {
                    'role': 'system',
                    'content': (
                        'You are speaking aloud in a Discord voice channel. Reply in one to '
                        'three short sentences. No markdown, lists, or tool calls.'
                    ),
                },
                {'role': 'user', 'content': user_input},
            ]
            response = provider.chat_completion(messages, tools=None, generation_params=gen_params)
            logger.debug(
                'Voice conversation LLM reply via %s model=%s',
                provider_key,
                gen_params.get('model'),
            )
            return str(getattr(response, 'content', '') or '').strip()
        except Exception as exc:
            logger.warning('Voice conversation LLM call failed: %s', exc)
            return ''
