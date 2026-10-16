"""Import core/tts/tts_server.py with no GPU model, no disk cache and no
network. The server boots at import (model + default voice), so tests stub
kokoro (a MagicMock carrying the one attribute the server reads,
KModel.MODEL_NAMES) and answer the hub's cache lookup with a stub path for
the duration of the import. Shared by test_tts.py and
test_kokoro_voice_cache.py; whichever is collected first pays the import."""
import sys
from unittest.mock import MagicMock, patch


def import_tts_server():
    if 'kokoro' not in sys.modules:
        kokoro = MagicMock()
        kokoro.KModel.MODEL_NAMES = {'hexgrad/Kokoro-82M': 'kokoro-v1_0.pth'}
        sys.modules['kokoro'] = kokoro
    with patch('huggingface_hub.try_to_load_from_cache', lambda repo, fn: f'/stub/{fn}'):
        import core.tts.tts_server as ts
    return ts
