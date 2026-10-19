import config
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

if config.WAKE_WORD_ENABLED:
    from .audio_recorder import AudioRecorder
    from .wake_detector import WakeWordDetector
else:
    from .wakeword_null import NullAudioRecorder as AudioRecorder
    from .wakeword_null import NullWakeWordDetector as WakeWordDetector

from .library import BUILTIN as BUILTIN_MODELS, CORE_DIR as CORE_MODELS_DIR, USER_DIR as USER_MODELS_DIR


def get_available_models():
    """
    Discover available wakeword models, from the library (core.wakeword.library).

    Returns dict with:
        - builtin: list of built-in model names (OWW bundled)
        - core: list of {name, path, type} for Sapphire-bundled models
        - custom: list of {name, path, type} for user models (path relative to the Sapphire folder)
        - all: the names this computer's detector can load (openWakeWord, .onnx), for the dropdown.
          A .tflite in the library is a microWakeWord model for an ESP32: listed in core/custom
          with type 'tflite', never offered to the desktop detector (2026-10-19).
    """
    from . import library
    rows = library.entries()
    core_models = [{'name': e['name'], 'path': e['path'], 'type': e['family']} for e in rows if e['source'] == 'core']
    custom_models = []
    for e in rows:
        if e['source'] != 'user':
            continue
        try:
            rel = str(Path(e['path']).relative_to(library.PROJECT_ROOT))
        except ValueError:
            rel = e['path']
        custom_models.append({'name': e['name'], 'path': rel, 'type': e['family']})
    all_models = list(BUILTIN_MODELS)
    usable = {(e['name'], e['family']) for e in rows if e.get('ok', True)}
    for model in core_models + custom_models:
        if model['type'] == 'onnx' and (model['name'], 'onnx') in usable and model['name'] not in all_models:
            all_models.append(model['name'])
    return {'builtin': BUILTIN_MODELS, 'core': core_models, 'custom': custom_models, 'all': all_models}



def resolve_model_path(model_name):
    """
    Resolve a model name to its path for THIS computer's detector (openWakeWord).

    The user's own file wins: user/wakeword/models/ first, then core/wakeword/models/, then the built-in names
    (which OWW resolves itself). So a wake word someone trained under the same name as a shipped model (the
    Wakeword Maker installs "hey sapphire" as hey_sapphire) is the one that listens. 2026-10-05.
    Only .onnx files are the desktop's (a .tflite in the library is a microWakeWord model for an ESP32,
    which this detector cannot load: a satellite install of hey_sapphire used to hide the shipped onnx and
    leave the desktop deaf, 2026-10-19); .tflite is looked at only when WAKEWORD_FRAMEWORK says so.
    For builtins without a user file, returns just the name; otherwise the full path.
    """
    exts = ['.onnx'] + (['.tflite'] if getattr(config, 'WAKEWORD_FRAMEWORK', 'onnx') == 'tflite' else [])
    for ext in exts:
        for folder in (USER_MODELS_DIR, CORE_MODELS_DIR):
            if folder.exists():
                for model_file in folder.rglob(f'{model_name}{ext}'):
                    return str(model_file)

    # Fallback: a builtin name, or a path
    return model_name

__all__ = ['AudioRecorder', 'WakeWordDetector', 'get_available_models', 'resolve_model_path', 'BUILTIN_MODELS', 'CORE_MODELS_DIR', 'USER_MODELS_DIR']