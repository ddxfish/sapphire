import asyncio

import pytest

from plugins.discord.transport.discord_transport import DiscordTransport


class FakeChannel:
    def __init__(self):
        self.id = 12345
        self.sent = []
        self.typing_count = 0

    async def send(self, content, reference=None, file=None):
        self.sent.append({'content': content, 'reference': reference, 'file': file})
        return type('Msg', (), {'id': len(self.sent)})()

    async def trigger_typing(self):
        self.typing_count += 1


class FakeClient:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if int(channel_id) == self._channel.id else None


def test_execution_sends_messages_on_daemon_loop():
    import discord
    if not hasattr(discord, 'MessageReference') and not hasattr(discord, 'Object'):
        pytest.skip('discord.py not available in test environment')

    async def run():
        channel = FakeChannel()
        loop = asyncio.get_running_loop()
        transport = DiscordTransport(loop=loop, client_factory=lambda **kwargs: FakeClient(channel))
        transport._accounts['alpha'] = {
            'name': 'alpha',
            'state': 'connected',
            'client': FakeClient(channel),
        }
        sent = await transport.execution.send_message(
            channel.id,
            'hello world',
            account_name='alpha',
            reply_to_message_id='99',
        )
        assert len(sent) == 1
        assert channel.sent[0]['content'] == 'hello world'
        assert channel.sent[0]['reference'] is not None
        await transport.execution.trigger_typing('alpha', channel.id)
        assert channel.typing_count == 1

    asyncio.run(run())


def test_build_reference_ignores_non_numeric_message_ids():
    from plugins.discord.transport.discord_execution import _build_reference
    import discord
    assert _build_reference(12345, 'task-followup-1') is None
    assert _build_reference(12345, '') is None
    if hasattr(discord, 'MessageReference') or hasattr(discord, 'Object'):
        assert _build_reference(12345, '1521787194761678918') is not None
