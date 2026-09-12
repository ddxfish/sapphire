from plugins.discord.sapphire.scheduler_bridge import SapphireSchedulerBridge


class FakeLoader:
    def __init__(self, task):
        self.task = task
        self.calls = []

    def get_enabled_daemon_task(self, event_name, account=None):
        self.calls.append((event_name, account))
        return self.task


def test_daemon_task_llm_returns_pinned_provider():
    bridge = SapphireSchedulerBridge(FakeLoader({'provider': 'claude', 'model': 'claude-sonnet-5'}))
    assert bridge.daemon_task_llm('discord_message', account='alpha') == ('claude', 'claude-sonnet-5')


def test_daemon_task_llm_auto_task_maps_to_empty():
    bridge = SapphireSchedulerBridge(FakeLoader({'provider': 'auto', 'model': ''}))
    assert bridge.daemon_task_llm('discord_message') == ('', '')


def test_daemon_task_llm_no_task_maps_to_empty():
    bridge = SapphireSchedulerBridge(FakeLoader(None))
    assert bridge.daemon_task_llm('discord_message') == ('', '')


def test_daemon_task_llm_loader_without_method():
    bridge = SapphireSchedulerBridge(object())
    assert bridge.daemon_task_llm('discord_message') == ('', '')
