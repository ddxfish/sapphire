from __future__ import annotations


class DiscordCommandService:
    def __init__(self, *, conversation_service, profile_service=None, memory_service=None):
        self.conversation_service = conversation_service
        self.profile_service = profile_service
        self.memory_service = memory_service

    def handle(self, command_name: str, *, account_name: str, channel_id: str, message_id: str, content: str, guild_id: str = '', guild_name: str = '', user_id: str = '', username: str = ''):
        context = {
            'account_name': account_name,
            'channel_id': channel_id,
            'message_id': message_id,
            'guild_id': guild_id,
            'guild_name': guild_name,
            'user_id': user_id,
            'username': username,
        }
        if command_name in {'ask', 'summarize'}:
            return self.conversation_service.queue_slash_command(command_name, content, context)
        if command_name == 'remember':
            if not self.profile_service or not self.memory_service:
                return {'status': 'unavailable', 'context': context}
            if not content.strip():
                return {'status': 'empty', 'context': context}
            self.profile_service.remember_fact(account_name, user_id, content.strip(), source='explicit')
            memory_id = self.memory_service.pin_memory(
                account_name, guild_id, channel_id, user_id, username, content.strip(),
            )
            return {'status': 'recorded', 'content': content, 'memory_id': memory_id, 'context': context}
        if command_name == 'forget-me':
            if not self.profile_service or not self.memory_service:
                return {'status': 'unavailable', 'context': context}
            self.profile_service.forget_user(account_name, user_id)
            self.memory_service.forget_user(account_name, user_id)
            return {'status': 'forgotten', 'context': context}
        raise ValueError(f'Unsupported slash command: {command_name}')
