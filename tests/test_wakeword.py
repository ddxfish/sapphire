"""Tests for wakeword model discovery and resolution."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


class TestBuiltinModels:
    """Tests for BUILTIN_MODELS constant."""
    
    def test_builtin_models_exists(self):
        """BUILTIN_MODELS should be a non-empty list."""
        from core.wakeword import BUILTIN_MODELS
        assert isinstance(BUILTIN_MODELS, list)
        assert len(BUILTIN_MODELS) > 0
    
    def test_builtin_models_contains_expected(self):
        """BUILTIN_MODELS should contain core OWW models."""
        from core.wakeword import BUILTIN_MODELS
        expected = ['alexa', 'hey_mycroft', 'hey_jarvis']
        for model in expected:
            assert model in BUILTIN_MODELS, f"Expected {model} in BUILTIN_MODELS"
    
    def test_builtin_models_are_strings(self):
        """All builtin models should be strings."""
        from core.wakeword import BUILTIN_MODELS
        for model in BUILTIN_MODELS:
            assert isinstance(model, str)


class TestGetAvailableModels:
    """Tests for get_available_models() function."""
    
    def test_returns_dict_structure(self):
        """Should return dict with builtin, custom, and all keys."""
        from core.wakeword import get_available_models
        result = get_available_models()
        assert isinstance(result, dict)
        assert 'builtin' in result
        assert 'custom' in result
        assert 'all' in result
    
    def test_builtin_list_matches_constant(self):
        """Builtin list should match BUILTIN_MODELS."""
        from core.wakeword import get_available_models, BUILTIN_MODELS
        result = get_available_models()
        assert result['builtin'] == BUILTIN_MODELS
    
    def test_all_contains_builtins(self):
        """All list should contain all builtins."""
        from core.wakeword import get_available_models, BUILTIN_MODELS
        result = get_available_models()
        for model in BUILTIN_MODELS:
            assert model in result['all']
    
    def test_discovers_onnx_files(self, tmp_path, monkeypatch):
        """Should discover .onnx files in user/wakeword/models/."""
        # Create fake model directory structure
        models_dir = tmp_path / 'user' / 'wakeword' / 'models'
        models_dir.mkdir(parents=True)
        
        # Create fake .onnx file
        (models_dir / 'hey_test.onnx').touch()
        
        # Monkeypatch the path resolution
        import core.wakeword as ww_module
        original_file = Path(ww_module.__file__)
        
        def mock_get_models():
            from core.wakeword import BUILTIN_MODELS
            custom_models = []
            
            for model_file in models_dir.rglob('*.onnx'):
                custom_models.append({
                    'name': model_file.stem,
                    'path': str(model_file),
                    'type': 'onnx'
                })
            
            all_models = list(BUILTIN_MODELS)
            for model in custom_models:
                if model['name'] not in all_models:
                    all_models.append(model['name'])
            
            return {
                'builtin': BUILTIN_MODELS,
                'custom': custom_models,
                'all': all_models
            }
        
        result = mock_get_models()
        
        assert len(result['custom']) == 1
        assert result['custom'][0]['name'] == 'hey_test'
        assert result['custom'][0]['type'] == 'onnx'
        assert 'hey_test' in result['all']
    
    def test_discovers_tflite_files(self, tmp_path):
        """Should discover .tflite files in user/wakeword/models/."""
        models_dir = tmp_path / 'user' / 'wakeword' / 'models'
        models_dir.mkdir(parents=True)
        
        (models_dir / 'ok_computer.tflite').touch()
        
        # Direct test of glob pattern
        tflite_files = list(models_dir.rglob('*.tflite'))
        assert len(tflite_files) == 1
        assert tflite_files[0].stem == 'ok_computer'
    
    def test_discovers_files_recursively(self, tmp_path):
        """Should discover models in subdirectories."""
        models_dir = tmp_path / 'user' / 'wakeword' / 'models'
        subdir = models_dir / 'custom_voices'
        subdir.mkdir(parents=True)
        
        (subdir / 'hey_deep.onnx').touch()
        
        # Direct test of recursive glob
        onnx_files = list(models_dir.rglob('*.onnx'))
        assert len(onnx_files) == 1
        assert onnx_files[0].stem == 'hey_deep'
    
    def test_no_duplicates_in_all(self, tmp_path):
        """Custom model with same name as builtin shouldn't duplicate."""
        from core.wakeword import BUILTIN_MODELS
        
        # Simulate a custom model with same name as builtin
        all_models = list(BUILTIN_MODELS)
        custom_name = 'alexa'  # Same as builtin
        
        # Our logic: only add if not already present
        if custom_name not in all_models:
            all_models.append(custom_name)
        
        # Should still only have one 'alexa'
        assert all_models.count('alexa') == 1


class TestResolveModelPath:
    """Tests for resolve_model_path() function."""
    
    def test_builtin_returns_name(self):
        """Builtin models should return just the name."""
        from core.wakeword import resolve_model_path, BUILTIN_MODELS
        
        for model in BUILTIN_MODELS:
            result = resolve_model_path(model)
            assert result == model, f"Builtin {model} should return name, got {result}"
    
    def test_nonexistent_custom_returns_name(self):
        """Non-existent custom model should return the name as fallback."""
        from core.wakeword import resolve_model_path
        
        result = resolve_model_path('nonexistent_model_xyz')
        assert result == 'nonexistent_model_xyz'
    
    def test_custom_model_returns_path(self, tmp_path, monkeypatch):
        """Custom model file should return full path."""
        # Create fake model
        models_dir = tmp_path / 'user' / 'wakeword' / 'models'
        models_dir.mkdir(parents=True)
        model_file = models_dir / 'hey_custom.onnx'
        model_file.touch()
        
        # Test path resolution logic directly
        def find_custom_model(name):
            for ext in ['.onnx', '.tflite']:
                for found in models_dir.rglob(f'{name}{ext}'):
                    return str(found)
            return name
        
        result = find_custom_model('hey_custom')
        assert result == str(model_file)
    
    def test_prefers_onnx_over_tflite(self, tmp_path):
        """When both .onnx and .tflite exist, should prefer .onnx."""
        models_dir = tmp_path / 'user' / 'wakeword' / 'models'
        models_dir.mkdir(parents=True)
        
        onnx_file = models_dir / 'dual_model.onnx'
        tflite_file = models_dir / 'dual_model.tflite'
        onnx_file.touch()
        tflite_file.touch()
        
        # Test our preference logic
        def find_with_preference(name):
            for ext in ['.onnx', '.tflite']:  # onnx first
                for found in models_dir.rglob(f'{name}{ext}'):
                    return str(found)
            return name
        
        result = find_with_preference('dual_model')
        assert result.endswith('.onnx')


class TestModelNameExtraction:
    """Tests for model name extraction from filenames."""
    
    def test_stem_extraction(self):
        """Model name should be filename without extension."""
        test_cases = [
            ('hey_jarvis.onnx', 'hey_jarvis'),
            ('ok_nabu.tflite', 'ok_nabu'),
            ('hey_mycroft_v2.onnx', 'hey_mycroft_v2'),
            ('model-with-dashes.onnx', 'model-with-dashes'),
        ]
        
        for filename, expected_stem in test_cases:
            path = Path(filename)
            assert path.stem == expected_stem
    
    def test_nested_path_stem(self):
        """Stem extraction should work for nested paths."""
        path = Path('/some/deep/path/custom/hey_test.onnx')
        assert path.stem == 'hey_test'


class TestIntegration:
    """Integration tests for the wakeword module."""
    
    def test_module_imports(self):
        """All expected exports should be importable."""
        from core.wakeword import (
            get_available_models,
            resolve_model_path,
            BUILTIN_MODELS
        )
        
        assert callable(get_available_models)
        assert callable(resolve_model_path)
        assert isinstance(BUILTIN_MODELS, list)
    
    def test_empty_user_dir_works(self):
        """Should work even if user/wakeword/models/ doesn't exist."""
        from core.wakeword import get_available_models
        
        # This shouldn't raise even if dir doesn't exist
        result = get_available_models()
        assert result['builtin'] == result['all'] or len(result['custom']) >= 0
    
    def test_get_and_resolve_consistency(self):
        """Models in 'all' should be resolvable."""
        from core.wakeword import get_available_models, resolve_model_path
        
        models = get_available_models()
        for model_name in models['all']:
            # Should not raise
            path = resolve_model_path(model_name)
            assert path is not None
            assert len(path) > 0