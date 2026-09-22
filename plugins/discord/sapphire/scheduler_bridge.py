# The sources whose enabled task selects a bot for connection: Chat only /
# Greetings / All interactions (S1) and the Voice channel gate (S6, 2026-09-22).
MESSAGE_SOURCES = ('discord_message', 'discord_greetings', 'discord_all', 'discord_voice')


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
