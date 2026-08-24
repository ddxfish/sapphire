import asyncio
from unittest.mock import AsyncMock, MagicMock

from plugins.discord.runtime.lifecycle import LifecycleManager


class FakeVoiceTransport:
    def __init__(self):
        self.disconnect_async = AsyncMock(return_value={'status': 'disconnected'})
        self.disconnect_sync = MagicMock()

    def list_connections(self):
        return [{'account_name': 'remmi', 'channel_id': '1516753078223896600'}]


def test_stop_disconnects_voice_with_async_methods():
    async def run_test():
        container = MagicMock()
        container.voice_transport = FakeVoiceTransport()
        container.message_pipeline = None
        container.scheduler.stop = AsyncMock()
        container.transport.close_all = AsyncMock()
        container.sqlite_service.stop = MagicMock()
        container.health = MagicMock()

        await LifecycleManager().stop(container)

        container.voice_transport.disconnect_async.assert_awaited_once_with(
            'remmi',
            '1516753078223896600',
        )
        container.voice_transport.disconnect_sync.assert_not_called()

    asyncio.run(run_test())
