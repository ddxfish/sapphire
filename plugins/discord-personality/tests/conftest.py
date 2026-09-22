import sys
from pathlib import Path

# Sapphire root (has core/ and plugins/) and the plugin's own root (for the
# `discord_personality` package hooks.py imports). `.absolute()` on purpose.
PLUGIN_ROOT = Path(__file__).absolute().parents[1]


def _sapphire_root(start: Path):
    root = start
    while not (root / 'core').is_dir():
        if root.parent == root:
            return None
        root = root.parent
    return root


for path in (_sapphire_root(PLUGIN_ROOT), PLUGIN_ROOT):
    if path is not None and str(path) not in sys.path:
        sys.path.insert(0, str(path))
