class SapphireSettingsBridge:
    def __init__(self, plugin_loader, plugin_name: str):
        self.plugin_loader = plugin_loader
        self.plugin_name = plugin_name

    def get_plugin_settings(self) -> dict:
        if hasattr(self.plugin_loader, 'get_plugin_settings'):
            return dict(self.plugin_loader.get_plugin_settings(self.plugin_name) or {})
        return {}
