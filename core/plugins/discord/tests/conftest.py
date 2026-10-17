import sys
from pathlib import Path

# The Sapphire root (holds sapphire.py) on sys.path: core.* and the
# plugins.<name> namespace (plugins.py at the root) both resolve from there,
# whichever band this plugin lives in. Never "the dir that holds plugins/":
# with the system band under core/ that probe matches core/ itself and puts it
# on the path, shadowing routes, chat, hooks... `.absolute()` on purpose:
# `.resolve()` follows a symlinked plugin out of the tree.


def _sapphire_root(start: Path) -> Path:
    root = start.absolute()
    while not (root / 'sapphire.py').is_file():
        if root.parent == root:
            raise RuntimeError(f'Sapphire root (sapphire.py) not found above {start}')
        root = root.parent
    return root


_ROOT = str(_sapphire_root(Path(__file__).parent))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

