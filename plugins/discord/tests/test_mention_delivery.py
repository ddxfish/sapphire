from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.mention_map_service import MentionMapService


class FakeTransport:
    def __init__(self, mention_map_service=None):
        self.sent = []
        self._mention_map_service = mention_map_service

    def send_message_sync(self, channel_id, text, reply_to_message_id=None, account_name=None, guild_id=None):
        if self._mention_map_service:
            text = self._mention_map_service.apply_text(
                text,
                account_name or 'remmi',
                str(channel_id),
                guild_id=str(guild_id or ''),
            )
        self.sent.append({'channel_id': channel_id, 'text': text, 'guild_id': guild_id})
        return {'status': 'sent', 'messages': [{'message_id': 'm1'}]}

    def hold_typing_sync(self, *args, **kwargs):
        return None

    def add_reaction_sync(self, *args, **kwargs):
        return None


class FakeReplyStyle:
    def should_skip_auto_reply(self, message_id):
        return False

    def parse_llm_output(self, text, strip_thinking=True):
        from plugins.discord.conversation.reply_style_service import ParsedReply

        return ParsedReply(chunks=[text], reaction='', gif_query='')


def test_handle_llm_response_resolves_username_mentions(monkeypatch):
    mention_map_service = MentionMapService()
    mention_map_service.build_for_channel(
        'remmi',
        'c1',
        author_id='123456789012345678',
        username='ddxfish',
        display_name='ddxfish',
    )
    transport = FakeTransport(mention_map_service=mention_map_service)
    service = ConversationService(
        event_bridge=None,
        policy_service=None,
        prompt_context_service=None,
        trace_repository=type('T', (), {'record_trace': lambda *a, **k: None})(),
        reply_style_service=FakeReplyStyle(),
        transport=transport,
        mention_map_service=mention_map_service,
    )
    monkeypatch.setattr(
        'plugins.discord.conversation.conversation_service.deliver_gif_and_reaction',
        lambda **kwargs: None,
    )
    service.handle_llm_response(
        None,
        {
            'message_id': 'm-trigger',
            'account': 'remmi',
            'channel_id': 'c1',
            'guild_id': 'g1',
            'mention_map': mention_map_service.get_map('remmi', 'c1'),
        },
        'Hey @ddxfish — happy birthday!',
    )
    assert transport.sent[0]['text'] == 'Hey <@123456789012345678> — happy birthday!'
