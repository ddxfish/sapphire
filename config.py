"""
Configuration Proxy

Run the program once, then it creates user/settings.json 

"""

# Proxy all attribute access to settings_manager
from core.settings_manager import settings as _settings

def __getattr__(name):
    """Forward all config.SOMETHING to settings_manager"""
    return getattr(_settings, name)

# For backwards compatibility with 'key in config' checks
def __contains__(key):
    return key in _settings