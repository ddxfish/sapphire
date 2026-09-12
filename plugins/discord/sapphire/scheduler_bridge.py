class SapphireSchedulerBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader

    def active_daemon_accounts(self, event_name: str) -> set[str]:
        if hasattr(self.plugin_loader, 'active_daemon_accounts'):
            return set(self.plugin_loader.active_daemon_accounts(event_name) or [])
        return set()
