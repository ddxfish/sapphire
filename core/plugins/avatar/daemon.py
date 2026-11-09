# core/plugins/avatar/daemon.py - her body's shared brain (brain.py) lives
# with the plugin: up on load, down on unload or disable.


def start(plugin_loader, settings):
    from plugins.avatar import brain
    brain.start()


def stop():
    from plugins.avatar import brain
    brain.stop()
