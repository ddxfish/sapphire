# core/paths.py — the project's directory constants, defined once.
#
# Imports nothing, so anything in-process (core, routes, plugins, tests, tools)
# can import it before anything else. Plugins: import your root from here,
# never count `.parent`s — a chain one level short lands on a REAL folder
# (core/) and quietly builds core/user/..., so the mistake is silent. Both
# bands sit 3 deep today; the day that changes, counted chains break again.
#
# .absolute(), never .resolve(): a symlinked plugin dir has to stay inside the
# tree it is linked into, or every "is this under the project?" check lies.
from pathlib import Path

PROJECT_ROOT = Path(__file__).absolute().parent.parent
SYSTEM_PLUGINS_DIR = PROJECT_ROOT / "core" / "plugins"
USER_PLUGINS_DIR = PROJECT_ROOT / "user" / "plugins"
PLUGIN_STATE_DIR = PROJECT_ROOT / "user" / "plugin_state"
