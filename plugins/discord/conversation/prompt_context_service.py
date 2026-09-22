from __future__ import annotations

from plugins.discord.conversation.transcript_service import format_recent_history


class PromptContextService:
    """The reply's context: the channel transcript, keyed to the trigger's identity."""

    def __init__(self, *, message_repository):
        self.message_repository = message_repository

    def build(self, batch, trigger=None) -> dict:
        # One identity for the whole turn: the reply gates key off the newest
        # ADDRESSED message, so the transcript exclusion does too — not
        # observations[-1] (hunt 2.13.0, row 15).
        last = trigger or getattr(batch, 'trigger', None) or batch.observations[-1]
        rows = self.message_repository.get_recent_messages(last.account_name, last.channel_id, limit=20)
        return {
            'recent_history': format_recent_history(rows, exclude_message_id=last.message_id),
            'channel_id': last.channel_id,
            'channel_name': last.channel_name,
            'guild_name': last.guild_name,
            'guild_id': last.guild_id,
            'author_id': last.author_id,
            'attachments': last.attachments,
        }
