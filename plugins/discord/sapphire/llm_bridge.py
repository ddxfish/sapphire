class SapphireLlmBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader

    def available(self) -> bool:
        return hasattr(self.plugin_loader, 'register_reply_handler')
