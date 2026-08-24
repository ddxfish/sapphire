from plugins.discord.models.intentions import ReplyMessageIntention
from plugins.discord.transport.discord_commands import DiscordCommandService


class FakeConversation:
    def __init__(self):
        self.calls = []

    def queue_slash_command(self, command_name, content, context):
        self.calls.append((command_name, content, context))
        return ReplyMessageIntention(
            intention_type='reply_message',
            account_name='alpha',
            channel_id=context['channel_id'],
            message_id=context['message_id'],
            reason=f'slash:{command_name}',
            prompt=content,
        )


class FakeProfileService:
    def __init__(self):
        self.facts = []
        self.forgotten = []

    def remember_fact(self, account_name, user_id, content, source='explicit', confidence=1.0):
        self.facts.append((account_name, user_id, content))

    def forget_user(self, account_name, user_id):
        self.forgotten.append((account_name, user_id))


class FakeMemoryService:
    def __init__(self):
        self.pinned = []
        self.forgotten = []

    def pin_memory(self, account_name, guild_id, channel_id, author_id, username, content):
        self.pinned.append(content)
        return len(self.pinned)

    def forget_user(self, account_name, user_id):
        self.forgotten.append((account_name, user_id))


def test_ask_and_summarize_route_through_conversation():
    service = DiscordCommandService(conversation_service=FakeConversation())
    ask = service.handle('ask', account_name='alpha', channel_id='c1', message_id='m1', content='help')
    summarize = service.handle('summarize', account_name='alpha', channel_id='c1', message_id='m2', content='')

    assert ask.intention_type == 'reply_message'
    assert summarize.reason == 'slash:summarize'
    assert len(service.conversation_service.calls) == 2


def test_remember_and_forget_me_return_structured_results():
    profile = FakeProfileService()
    memory = FakeMemoryService()
    service = DiscordCommandService(
        conversation_service=FakeConversation(),
        profile_service=profile,
        memory_service=memory,
    )
    remember = service.handle(
        'remember',
        account_name='alpha',
        channel_id='c1',
        message_id='m1',
        content='I like tea',
        guild_id='g1',
        user_id='u1',
        username='alice',
    )
    forget_me = service.handle(
        'forget-me',
        account_name='alpha',
        channel_id='c1',
        message_id='m2',
        content='',
        user_id='u1',
    )

    assert remember['status'] == 'recorded'
    assert profile.facts[0][2] == 'I like tea'
    assert memory.pinned == ['I like tea']
    assert forget_me['status'] == 'forgotten'
    assert profile.forgotten == [('alpha', 'u1')]
    assert memory.forgotten == [('alpha', 'u1')]
