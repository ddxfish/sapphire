# core/plugins/browser/daemon.py - lifecycle only: Chromium starts on the
# first tool call (engine.py), and every lane closes with the plugin.


def start(plugin_loader, settings):
    from plugins.browser import engine
    engine.allow()
    engine.apply(settings)


def on_settings_saved(settings):
    """A save reaches open lanes now: the rails and self-login switches
    don't wait for her next tool call (race scout 2026-10-27)."""
    from plugins.browser import engine
    engine.apply(settings)


def stop():
    from plugins.browser import engine
    engine.shutdown()
