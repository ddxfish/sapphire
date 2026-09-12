import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SAPPHIRE_CANDIDATE = Path(__file__).resolve().parents[3]


def _ensure_plugins_import_path() -> None:
    # Installed under Sapphire: <root>/plugins/discord/tests → parents[3] is Sapphire root.
    if (SAPPHIRE_CANDIDATE / 'plugins' / 'discord').exists():
        root = str(SAPPHIRE_CANDIDATE)
        if root not in sys.path:
            sys.path.insert(0, root)
        return

    # Standalone WIP checkout: expose this repo as plugins.discord via a temp shim.
    shim = Path(tempfile.gettempdir()) / 'discord-zeebie-pytest-shim'
    plugins_dir = shim / 'plugins'
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / '__init__.py').touch(exist_ok=True)
    link = plugins_dir / 'discord'
    if link.is_symlink() or link.exists():
        if not link.is_symlink() or link.resolve() != PLUGIN_ROOT.resolve():
            link.unlink()
            link.symlink_to(PLUGIN_ROOT, target_is_directory=True)
    else:
        link.symlink_to(PLUGIN_ROOT, target_is_directory=True)
    shim_str = str(shim)
    if shim_str not in sys.path:
        sys.path.insert(0, shim_str)

    # Optional local Sapphire tree for imports like core.stt.*
    for candidate in (
        Path.home() / 'Documents' / 'sapphire',
        PLUGIN_ROOT.parents[1] / 'sapphire',
        Path('/home/zeebie/Documents/sapphire'),
    ):
        if (candidate / 'core').is_dir():
            cand = str(candidate)
            if cand not in sys.path:
                sys.path.append(cand)
            break


_ensure_plugins_import_path()
