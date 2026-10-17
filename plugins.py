# plugins.py — the `plugins.<name>` import namespace, for both plugin bands.
#
# A module with __path__ is a package: `plugins.<name>...` resolves by searching
# the shipped band first, then the user band, so a plugin's own imports work
# wherever it lives — in the app, in a standalone pytest run, in a CLI script.
# When a user-band plugin shadows a shipped name, the loader rebinds that one
# name to the copy that actually loaded (plugin_loader._install_pkg_alias).
#
# Never import a plugin by its disk path (core.plugins.<name>): that would be a
# second module object with its own locks and state. Nothing else lives here.
from core.paths import SYSTEM_PLUGINS_DIR, USER_PLUGINS_DIR

__path__ = [str(SYSTEM_PLUGINS_DIR), str(USER_PLUGINS_DIR)]
