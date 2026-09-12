class SapphireSchedulerBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader

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
