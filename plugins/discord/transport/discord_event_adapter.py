from __future__ import annotations

import time

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.models.observations import TextMessageObservation, TypingObservation


class DiscordEventAdapter:
    def __init__(self, *, image_lane=None, settings_store=None, mention_map_service=None):
        self.image_lane = image_lane
        self.settings_store = settings_store
        self.mention_map_service = mention_map_service

    def adapt_message_event(self, account_name: str, self_user_id: int | str | None, message, *,
                            fetch_images: bool = True) -> TextMessageObservation | None:
        author_id = str(getattr(message.author, 'id', ''))
        if self_user_id is not None and author_id == str(self_user_id):
            return None
        guild = getattr(message, 'guild', None)
        channel = message.channel
        attachments = [
            {'url': getattr(item, 'url', ''), 'filename': getattr(item, 'filename', ''),
             'content_type': getattr(item, 'content_type', '')}
            for item in getattr(message, 'attachments', [])
        ]
        mention_user_ids = [str(getattr(item, 'id', '')) for item in getattr(message, 'mentions', [])
                            if getattr(item, 'id', None)]
        self_id = str(self_user_id) if self_user_id is not None else ''
        reference = getattr(message, 'reference', None)
        observation = TextMessageObservation(
            observation_id=f'message:{account_name}:{message.id}',
            account_name=account_name,
            guild_id=str(getattr(guild, 'id', '') or ''),
            guild_name=getattr(guild, 'name', '') or 'DM',
            channel_id=str(channel.id),
            channel_name=getattr(channel, 'name', 'DM') or 'DM',
            author_id=author_id,
            username=getattr(message.author, 'name', '') or '',
            display_name=getattr(message.author, 'display_name', '') or getattr(message.author, 'name', ''),
            message_id=str(message.id),
            content=getattr(message, 'content', '') or '',
            clean_content=getattr(message, 'clean_content', '') or getattr(message, 'content', '') or '',
            created_at=time.time(),
            is_dm=guild is None,
            mentioned=bool(self_id and self_id in mention_user_ids),
            author_is_bot=bool(getattr(message.author, 'bot', False)),
            mention_user_ids=mention_user_ids,
            attachments=attachments,
            reply_to_message_id=str(getattr(reference, 'message_id', '') or ''),
        )
        # Ignored channels are dropped BEFORE anything observes them (broadsword
        # H6): "fully ignored" means never stored, never counted.
        if self.settings_store and is_channel_ignored(observation.account_name, observation.channel_id,
                                                      self.settings_store.resolve()):
            return None
        if fetch_images:
            self.fetch_images(observation)
        if self.mention_map_service:
            self.mention_map_service.update_from_discord_message(account_name, message, observation)
        # Add-ons hear every inbound message through the discord_message_observed
        # HOOK (hooks_out, fired by the pipeline). The old second door — the same
        # payload on core's event bus — had no subscriber and no ephemeral flag,
        # so every message body sat in the bus replay ring for any later web
        # tab (found by the S7 audit, 2026-09-22). One door.
        return observation

    async def adapt_typing_event(self, account_name: str, self_user_id: int | str | None, channel, user,
                                 when=None) -> TypingObservation | None:
        author_id = str(getattr(user, 'id', ''))
        if self_user_id is not None and author_id == str(self_user_id):
            return None
        guild = getattr(channel, 'guild', None)
        return TypingObservation(
            observation_id=f'typing:{account_name}:{author_id}:{getattr(channel, "id", "")}',
            account_name=account_name,
            guild_id=str(getattr(guild, 'id', '') or ''),
            guild_name=getattr(guild, 'name', '') or 'DM',
            channel_id=str(getattr(channel, 'id', '')),
            channel_name=getattr(channel, 'name', 'DM') or 'DM',
            author_id=author_id,
            username=getattr(user, 'name', '') or '',
            display_name=getattr(user, 'display_name', '') or getattr(user, 'name', ''),
            created_at=time.time(),
            is_dm=guild is None,
        )

    def fetch_images(self, observation: TextMessageObservation) -> int:
        """Pull the message's images into the lane's cache. Blocking (network):
        the transport calls it on a worker thread after adapt_message_event(
        fetch_images=False) — hunt C2: this used to run inline on the daemon loop."""
        if not self.image_lane or not observation.attachments:
            return 0
        settings = self.settings_store.resolve() if self.settings_store else None
        return self.image_lane.fetch(observation, settings)
