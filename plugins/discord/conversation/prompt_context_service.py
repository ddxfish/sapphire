from __future__ import annotations

import re

from plugins.discord.conversation.transcript_service import format_recent_history


class PromptContextService:
    def __init__(
        self,
        *,
        message_repository,
        observation_interpreter=None,
        memory_service=None,
        profile_service=None,
        attention_service=None,
        media_service=None,
        trace_service=None,
        edit_history_service=None,
    ):
        self.message_repository = message_repository
        self.observation_interpreter = observation_interpreter
        self.memory_service = memory_service
        self.profile_service = profile_service
        self.attention_service = attention_service
        self.media_service = media_service
        self.trace_service = trace_service
        self.edit_history_service = edit_history_service

    def build(self, batch) -> dict:
        last = batch.observations[-1]
        transcript_rows = self.message_repository.get_recent_messages(last.account_name, last.channel_id, limit=20)
        media_by_message = {}
        if self.media_service:
            message_ids = [str(row.get('message_id') or '') for row in transcript_rows]
            media_by_message = self.media_service.build_context_map(message_ids)
        transcript = format_recent_history(
            transcript_rows,
            media_by_message,
            exclude_message_id=last.message_id,
        )
        interpretation = self.observation_interpreter.interpret(last) if self.observation_interpreter else {}
        context = {
            'recent_history': transcript,
            'channel_id': last.channel_id,
            'channel_name': last.channel_name,
            'guild_name': last.guild_name,
            'guild_id': last.guild_id,
            'author_id': last.author_id,
            'trigger': interpretation,
            'attachments': last.attachments,
        }
        if self.memory_service:
            recalled = self.memory_service.recall(
                last.account_name,
                last.guild_id,
                last.channel_id,
                last.clean_content[:80] or 'conversation',
                limit=5,
            )
            pinned = self.memory_service.get_pinned(last.account_name, guild_id=last.guild_id, limit=5)
            context['memory'] = {
                'recalled': recalled,
                'pinned': pinned,
            }
            if self.trace_service:
                self.trace_service.record_memory_injection({
                    'channel_id': last.channel_id,
                    'recalled': len(recalled),
                    'pinned': len(pinned),
                })
        if self.profile_service:
            profile_context = self.profile_service.build_context(last.account_name, last.author_id)
            affect = self.profile_service.get_affect(last.account_name)
            context['profile'] = profile_context
            context['affect'] = affect.to_dict()
        if self.attention_service:
            context['activation'] = self.attention_service.channel_activation(last.account_name, last.channel_id)
        if self.edit_history_service:
            edit_hint = self.edit_history_service.build_prompt_hint(last.account_name, last.channel_id)
            if edit_hint:
                context['edit_history_hint'] = edit_hint
        if self.media_service:
            media_context = []
            media_message_id = self._media_context_message_id(batch)
            if media_message_id:
                media_context = self.media_service.build_context(media_message_id)
            if media_context:
                context['media'] = media_context
            if self.trace_service and media_context:
                self.trace_service.record_media_interpreted({
                    'message_id': media_message_id,
                    'artifacts': len(media_context),
                })
        return context

    def _media_context_message_id(self, batch) -> str:
        last = batch.observations[-1]
        if last.attachments:
            return last.message_id

        reply_to = str(getattr(last, 'reply_to_message_id', '') or '')
        if reply_to and self.media_service and self.media_service.message_has_media(reply_to):
            return reply_to

        recent_media_message = self._most_recent_media_message(batch.observations[:-1])
        if recent_media_message:
            if self._is_explicit_media_follow_up(last.clean_content):
                return recent_media_message.message_id
            if reply_to == recent_media_message.message_id:
                return recent_media_message.message_id

        if self._is_explicit_media_follow_up(last.clean_content) and self.media_service:
            return self.media_service.get_recent_media_message_id(last.channel_id)

        return ''

    def _most_recent_media_message(self, observations) -> object | None:
        for observation in reversed(observations):
            if getattr(observation, 'attachments', None):
                return observation
        return None

    def _is_explicit_media_follow_up(self, text: str) -> bool:
        normalized = ' '.join((text or '').lower().split())
        if not normalized:
            return False

        explicit_phrases = (
            'what is this image',
            'what is this gif',
            'what is this picture',
            'what is this photo',
            'what is in this image',
            'what is in this picture',
            'what is in this photo',
            'what does this image show',
            'what does this picture show',
            'what does this photo show',
            'describe this image',
            'describe this picture',
            'describe this photo',
            'describe this gif',
            'can you see the image',
            'can you see this image',
            'can you see the picture',
            'can you see this picture',
            'can you see the photo',
            'can you see this photo',
            'can you see the gif',
            'can you see this gif',
            'do you see the image',
            'do you see this image',
            'do you see the picture',
            'do you see this picture',
        )
        if any(phrase in normalized for phrase in explicit_phrases):
            return True

        if re.search(r'\b(can|do) you see\b', normalized):
            return any(token in normalized for token in (
                'image', 'picture', 'photo', 'gif', ' pic', 'this', 'that', 'it',
            ))

        return False
