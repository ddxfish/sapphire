import asyncio

from plugins.discord.runtime.container import RuntimeContainer


class FakeLoader:
    def __init__(self):
        self.handlers = []

    def register_reply_handler(self, plugin_name, handler):
        self.handlers.append((plugin_name, handler))


def test_container_start_and_stop(tmp_path):
    async def run_test():
        loader = FakeLoader()
        container = RuntimeContainer(
            plugin_name="discord",
            plugin_loader=loader,
            settings={"database_path": str(tmp_path / "discord.sqlite3")},
            loop=asyncio.get_running_loop(),
        )
        await container.start()
        assert container.health.state == "ready"
        assert container.sqlite_service is not None
        assert container.transport is not None
        await container.stop()
        assert container.health.state == "stopped"

    asyncio.run(run_test())
