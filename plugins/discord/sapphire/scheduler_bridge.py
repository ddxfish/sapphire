# The daemon sources that select a bot for connection. Chat only / Greetings /
# All interactions (S1, 2026-09-22); S6 adds the voice gate.
MESSAGE_SOURCES = ('discord_message', 'discord_greetings', 'discord_all')


class SapphireSchedulerBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader

    def selected_accounts(self) -> set[str]:
        """Accounts with an enabled task on ANY message source — the bots to keep online."""
        selected: set[str] = set()
        for source in MESSAGE_SOURCES:
            selected |= set(self.active_daemon_accounts(source))
        return selected

    def active_daemon_accounts(self, event_name: str) -> set[str]:
        if hasattr(self.plugin_loader, 'active_daemon_accounts'):
            return set(self.plugin_loader.active_daemon_accounts(event_name) or [])
        return set()

    def daemon_task_llm(self, event_name: str, account: str = None) -> tuple[str, str]:
        """(provider, model) pinned on the enabled daemon task for this account.

        Returns ('', '') when there is no task, the task is on auto, or the
        loader can't answer — callers treat empty as "no daemon pin".
        """
        if not hasattr(self.plugin_loader, 'get_enabled_daemon_task'):
            return '', ''
        try:
            task = self.plugin_loader.get_enabled_daemon_task(event_name, account=account)
        except Exception:
            return '', ''
        if not task:
            return '', ''
        provider = str(task.get('provider') or '').strip()
        model = str(task.get('model') or '').strip()
        if provider.lower() == 'auto':
            return '', ''
        return provider, model
