import asyncio

from plugins.discord.runtime.container import RuntimeContainer
from plugins.discord.runtime.scheduler_loop import SchedulerLoop


class FakeLoader:
    def register_reply_handler(self, plugin_name, handler):
        pass


def test_container_restart_cycle(tmp_path):
    async def run_test():
        loader = FakeLoader()
        db = str(tmp_path / 'recovery.sqlite3')
        container = RuntimeContainer(
            plugin_name='discord',
            plugin_loader=loader,
            settings={'database_path': db},
            loop=asyncio.get_running_loop(),
        )
        await container.start()
        assert container.health.state == 'ready'
        await container.stop()
        assert container.health.state == 'stopped'

        container2 = RuntimeContainer(
            plugin_name='discord',
            plugin_loader=loader,
            settings={'database_path': db},
            loop=asyncio.get_running_loop(),
        )
        await container2.start()
        assert container2.health.state == 'ready'
        await container2.stop()

    asyncio.run(run_test())


def test_scheduler_survives_tick_exception():
    async def run_test():
        loop = SchedulerLoop(interval_seconds=0.01)
        failures = {'count': 0}

        def bad_tick():
            failures['count'] += 1
            if failures['count'] == 1:
                raise RuntimeError('degraded dependency')

        loop.set_tick_handler(bad_tick)
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()
        assert failures['count'] >= 1

    asyncio.run(run_test())
