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

# Built-in models bundled with openwakeword
BUILTIN_MODELS = ['alexa', 'hey_mycroft', 'hey_jarvis', 'hey_rhasspy', 'timer', 'weather']

def get_available_models():
    """
    Discover available wakeword models.
    
    Returns dict with:
        - builtin: list of built-in model names
        - custom: list of {name, path} dicts for user models
        - all: combined list of all model names for dropdown
    """
    custom_models = []
    
    # Scan user/wakeword/models/ for .onnx and .tflite files
    user_models_dir = Path(__file__).parent.parent.parent / 'user' / 'wakeword' / 'models'
    
    if user_models_dir.exists():
        for model_file in user_models_dir.rglob('*.onnx'):
            name = model_file.stem  # filename without extension
            rel_path = str(model_file.relative_to(Path(__file__).parent.parent.parent))
            custom_models.append({
                'name': name,
                'path': rel_path,
                'type': 'onnx'
            })
            logger.debug(f"Found custom wakeword model: {name} at {rel_path}")
        
        for model_file in user_models_dir.rglob('*.tflite'):
            name = model_file.stem
            rel_path = str(model_file.relative_to(Path(__file__).parent.parent.parent))
            custom_models.append({
                'name': name,
                'path': rel_path,
                'type': 'tflite'
            })
            logger.debug(f"Found custom wakeword model: {name} at {rel_path}")
    
    # Build combined list for dropdown (builtins first, then custom)
    all_models = list(BUILTIN_MODELS)
    for model in custom_models:
        if model['name'] not in all_models:
            all_models.append(model['name'])
    
    return {
        'builtin': BUILTIN_MODELS,
        'custom': custom_models,
        'all': all_models
    }

def resolve_model_path(model_name):
    """
    Resolve a model name to its path.
    
    For builtins, returns just the name (OWW handles it).
    For custom models, returns the full path.
    """
    if model_name in BUILTIN_MODELS:
        return model_name
    
    # Check custom models
    user_models_dir = Path(__file__).parent.parent.parent / 'user' / 'wakeword' / 'models'
    
    if user_models_dir.exists():
        # Try .onnx first, then .tflite
        for ext in ['.onnx', '.tflite']:
            for model_file in user_models_dir.rglob(f'{model_name}{ext}'):
                return str(model_file)
    
    # Fallback: assume it's a builtin or path
    return model_name

__all__ = ['AudioRecorder', 'WakeWordDetector', 'get_available_models', 'resolve_model_path', 'BUILTIN_MODELS']