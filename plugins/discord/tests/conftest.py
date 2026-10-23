import sys
from pathlib import Path

# The dir that holds plugins/discord — the Sapphire root in the system band,
# user/ in the user band. Then the Sapphire root itself (has core/) for core.*
# imports. `.absolute()` on purpose: `.resolve()` follows a symlinked plugin
# out of the tree. The old standalone-checkout branch (a temp-dir symlink shim
# plus a hardcoded developer home path) shipped in signed code — removed
# 2026-09-13 (hunt M14); the plugin lives in-tree now.
PLUGINS_PARENT = Path(__file__).absolute().parents[3]


def _ensure_plugins_import_path() -> None:
    if not (PLUGINS_PARENT / 'plugins' / 'discord').is_dir():
        raise RuntimeError(f'discord plugin tests expected plugins/discord under {PLUGINS_PARENT}')
    for path in (PLUGINS_PARENT, _sapphire_root(PLUGINS_PARENT)):
        if path is not None and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _sapphire_root(start: Path) -> Path | None:
    root = start
    while not (root / 'core').is_dir():
        if root.parent == root:
            return None
        root = root.parent
    return root


_ensure_plugins_import_path()

