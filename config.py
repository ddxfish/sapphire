"""
Configuration Proxy

Run the program once, then it creates user/settings.json 

"""

# Proxy all attribute access to settings_manager
from core.settings_manager import settings as _settings

def __getattr__(name):
    """Forward all config.SOMETHING to settings_manager"""
    return getattr(_settings, name)

def __dir__():
    """Enable IDE autocompletion by exposing available settings"""
    return list(_settings._config.keys())