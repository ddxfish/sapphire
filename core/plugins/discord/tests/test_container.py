"""The runtime container (S7): build, start, stop, restart, and the doors it wires."""
import asyncio

from plugins.discord.accounts import DiscordAccounts
from plugins.discord.runtime.container import RuntimeContainer
from plugins.discord.tests._fakes import FakeCreds, old_account_db


class FakeLoader:
    def __init__(self):
        self.handlers = []

    def register_reply_handler(self, plugin_name, handler):
        self.handlers.append((plugin_name, handler))

    def active_daemon_accounts(self, source):
        return set()


def _container(tmp, loop, creds=None):
    accounts = DiscordAccounts(store=creds or FakeCreds(), legacy_dir=tmp)
    return RuntimeContainer(plugin_name='discord', plugin_loader=FakeLoader(), settings={'scheduler_interval_seconds': 1},
                            loop=loop, accounts=accounts)


def test_container_start_stop_and_restart_on_the_same_accounts(tmp_path):
    async def run():
        creds = FakeCreds()
        c1 = _container(tmp_path, asyncio.get_running_loop(), creds)
        await c1.start()
        assert c1.health.state == 'ready' and c1._tick_task is not None
        assert c1.transport is not None and c1.transport.list_connected() == []
        await c1.stop()
        assert c1.health.state == 'stopped' and c1._tick_task is None
        c2 = _container(tmp_path, asyncio.get_running_loop(), creds)
        await c2.start()
        assert c2.health.state == 'ready'
        await c2.stop()
    asyncio.run(run())


def test_the_doors_are_wired_at_build_not_on_the_first_tick(tmp_path):
    async def run():
        c = _container(tmp_path, asyncio.get_running_loop())
        # <<HANG UP>> and every other leave reach the latch before any tick ran.
        assert c.discord_conversation_runner.leave_fn == c._leave_voice
        assert c.voice_service.on_leave == c.voice_auto_join_service.note_leave
        assert c.voice_gate.describe == c.transport.describe_voice_channel
        assert c.conversation_service.transport is c.transport
    asyncio.run(run())


def test_tick_survives_a_failing_step(tmp_path):
    async def run():
        c = _container(tmp_path, asyncio.get_running_loop())
        await c.start()
        c.greetings.tick = lambda: 1 / 0
        await c._tick()                       # the greetings failure is logged, the tick completes
        await c.stop()
    asyncio.run(run())


def test_refresh_settings_moves_the_batch_window(tmp_path):
    async def run():
        c = _container(tmp_path, asyncio.get_running_loop())
        c.settings_store.overrides = {'channel': {'batching_seconds': 3}}
        c.refresh_settings()
        assert c.batching_service.default_window_seconds == 3.0
    asyncio.run(run())


def test_start_imports_an_old_account_database_before_connecting(tmp_path):
    async def run():
        old_account_db(tmp_path / 'discord' / 'discord.sqlite3')
        c = _container(tmp_path, asyncio.get_running_loop())
        await c.start()
        assert c.account_repository.get_token('sapphire') == 'tok-1'
        assert (tmp_path / 'discord' / 'discord.sqlite3.imported').exists()
        await c.stop()
    asyncio.run(run())
