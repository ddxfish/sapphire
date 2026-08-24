from plugins.discord.models.intentions import UpdatePresenceIntention
from plugins.discord.proactive.proactive_executor import ProactiveExecutor
from plugins.discord.transport.discord_presence import DiscordPresenceService


class FakeTransport:
    def __init__(self, *, result=None):
        self.result = result or {'status': 'updated'}
        self.calls = 0

    async def change_presence_async(self, account_name, **kwargs):
        self.calls += 1
        return self.result


def test_presence_not_marked_when_transport_fails():
    presence = DiscordPresenceService()
    transport = FakeTransport(result={'status': 'error', 'error': 'not ready'})
    executor = ProactiveExecutor(transport=transport, presence_service=presence)
    intention = UpdatePresenceIntention(
        intention_type='update_presence',
        account_name='alpha',
        channel_id='',
        message_id='',
        reason='test',
        status='idle',
        activity='custom: sleeping',
        metadata={'mode': 'sleep'},
    )

    import asyncio
    asyncio.run(executor.execute_presence_async(intention))

    assert transport.calls == 1
    assert presence._last_mode.get('alpha') is None


def test_presence_marked_after_successful_transport_update():
    presence = DiscordPresenceService()
    transport = FakeTransport()
    executor = ProactiveExecutor(transport=transport, presence_service=presence)
    intention = UpdatePresenceIntention(
        intention_type='update_presence',
        account_name='alpha',
        channel_id='',
        message_id='',
        reason='test',
        status='idle',
        activity='custom: sleeping',
        metadata={'mode': 'sleep'},
    )

    import asyncio
    asyncio.run(executor.execute_presence_async(intention))

    assert presence._last_mode.get('alpha') == 'sleep'


class AsyncMessageTransport:
    def __init__(self):
        self.sync_calls = 0
        self.async_calls = 0

    def send_message_sync(self, channel, text, reply_to_message_id=None, account_name=None):
        self.sync_calls += 1
        return {'status': 'sent', 'channel_id': str(channel)}

    async def send_message_async(self, channel, text, reply_to_message_id=None, account_name=None):
        self.async_calls += 1
        return {'status': 'sent', 'channel_id': str(channel)}


def test_execute_async_uses_async_message_transport():
    from plugins.discord.models.intentions import GreetChannelIntention

    transport = AsyncMessageTransport()
    executor = ProactiveExecutor(transport=transport)
    intention = GreetChannelIntention(
        intention_type='greet_channel',
        account_name='alpha',
        channel_id='1475944594616352778',
        message_id='',
        reason='morning_greeting',
        prompt='Good morning!',
    )

    import asyncio
    result = asyncio.run(executor.execute_async(intention))

    assert result.get('status') == 'sent'
    assert transport.async_calls == 1
    assert transport.sync_calls == 0
