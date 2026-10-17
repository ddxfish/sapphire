"""Standalone test config for the twilio-voice plugin.

Puts the repo root on sys.path so plugin modules loaded by file path can still
`from core.audio import ...`. Lets `pytest plugins/twilio-voice/tests/` run alone.
The plugin dir name has a hyphen, so its modules are loaded by path, not import.
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve()
while _root != _root.parent and not (_root / "core").is_dir():
    _root = _root.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
