"""
Phase 3: API Routes Tests

Tests the internal Flask API routes using test client.
Focus on auth, endpoint structure, and response formats.

Run with: pytest tests/test_api_routes.py -v
"""
import pytest
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from flask import Flask

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Mock tiktoken before any imports that might need it
mock_tiktoken = MagicMock()
mock_encoding = MagicMock()
mock_encoding.encode.return_value = []  # Empty list for token counting
mock_tiktoken.get_encoding.return_value = mock_encoding
mock_tiktoken.encoding_for_model.return_value = mock_encoding
sys.modules['tiktoken'] = mock_tiktoken

# Mock openai before any imports that might need it
mock_openai = MagicMock()
sys.modules['openai'] = mock_openai


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_system():
    """Create a mock system instance with all required attributes."""
    system = MagicMock()
    
    # LLM Chat
    system.llm_chat = MagicMock()
    system.llm_chat.session_manager = MagicMock()
    system.llm_chat.session_manager.get_messages.return_value = []
    system.llm_chat.session_manager.get_messages_for_display.return_value = []
    system.llm_chat.session_manager.get_messages_for_llm.return_value = []
    system.llm_chat.session_manager.get_chat_settings.return_value = {'spice_enabled': True}
    system.llm_chat.function_manager = MagicMock()
    system.llm_chat.function_manager.get_enabled_function_names.return_value = ['test_func']
    system.llm_chat.function_manager.get_current_ability_info.return_value = {
        'name': 'all', 'status': 'ok', 'function_count': 1
    }
    system.llm_chat.function_manager.has_network_tools_enabled.return_value = False
    system.llm_chat.module_loader = MagicMock()
    system.llm_chat.module_loader.get_module_list.return_value = []
    system.llm_chat.get_system_prompt_template.return_value = "Test prompt"
    
    # TTS
    system.tts = MagicMock()
    
    # Whisper
    system.whisper_client = MagicMock()
    
    return system


@pytest.fixture
def app_with_api(mock_system):
    """Create a Flask app with the API blueprint."""
    from core.api import create_api
    
    app = Flask(__name__)
    app.config['TESTING'] = True
    
    # Mock the get_password_hash to return a known value
    with patch('core.api.prompts') as mock_prompts:
        mock_prompts.get_current_state.return_value = {'mode': 'monolith'}
        mock_prompts.get_active_preset_name.return_value = 'default'
        mock_prompts.get_prompt_char_count.return_value = 100
        mock_prompts.get_prompt.return_value = {'content': 'Test prompt'}
        mock_prompts.get_current_spice.return_value = None
        mock_prompts.is_assembled_mode.return_value = False
        
        bp = create_api(mock_system)
        app.register_blueprint(bp, url_prefix='/api')
    
    return app


@pytest.fixture
def client(app_with_api):
    """Create a test client."""
    return app_with_api.test_client()


@pytest.fixture
def valid_api_key():
    """Return a valid API key for testing."""
    return "test_api_key_hash"


# =============================================================================
# Auth Tests
# =============================================================================

class TestAuth:
    """Test API key authentication."""
    
    def test_request_without_api_key_returns_401(self, client):
        """Requests without API key should return 401."""
        with patch('core.setup.get_password_hash', return_value="test_hash"):
            response = client.get('/api/health')
            assert response.status_code == 401
    
    def test_request_with_invalid_api_key_returns_401(self, client):
        """Requests with wrong API key should return 401."""
        with patch('core.setup.get_password_hash', return_value="correct_hash"):
            response = client.get('/api/health', headers={'X-API-Key': 'wrong_key'})
            assert response.status_code == 401
    
    def test_request_with_valid_api_key_succeeds(self, client):
        """Requests with correct API key should succeed."""
        with patch('core.setup.get_password_hash', return_value="correct_hash"):
            response = client.get('/api/health', headers={'X-API-Key': 'correct_hash'})
            assert response.status_code == 200
    
    def test_missing_server_api_key_returns_500(self, client):
        """Server without API key configured should return 500."""
        with patch('core.setup.get_password_hash', return_value=None):
            response = client.get('/api/health')
            assert response.status_code == 500


# =============================================================================
# Health Endpoint Tests
# =============================================================================

class TestHealthEndpoint:
    """Test /health endpoint."""
    
    def test_health_returns_ok(self, client):
        """Health endpoint should return status ok."""
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.get('/api/health', headers={'X-API-Key': 'test'})
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'ok'


# =============================================================================
# History Endpoint Tests
# =============================================================================

class TestHistoryEndpoints:
    """Test /history endpoints."""
    
    def test_get_history_returns_messages_and_context(self, client, mock_system):
        """GET /history should return messages array and context info."""
        test_messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there!'}
        ]
        mock_system.llm_chat.session_manager.get_messages_for_display.return_value = test_messages
        mock_system.llm_chat.session_manager.get_messages_for_llm.return_value = test_messages
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.config') as mock_config:
                mock_config.CONTEXT_LIMIT = 32000
                response = client.get('/api/history', headers={'X-API-Key': 'test'})
                
                assert response.status_code == 200
                data = json.loads(response.data)
                assert 'messages' in data
                assert 'context' in data
                assert isinstance(data['messages'], list)
                assert len(data['messages']) == 2
    
    def test_get_history_empty(self, client, mock_system):
        """GET /history with no messages should return empty messages list."""
        mock_system.llm_chat.session_manager.get_messages_for_display.return_value = []
        mock_system.llm_chat.session_manager.get_messages_for_llm.return_value = []
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.config') as mock_config:
                mock_config.CONTEXT_LIMIT = 32000
                response = client.get('/api/history', headers={'X-API-Key': 'test'})
                
                assert response.status_code == 200
                data = json.loads(response.data)
                assert 'messages' in data
                assert data['messages'] == []
    
    def test_get_history_context_has_required_fields(self, client, mock_system):
        """GET /history context should have used, limit, percent."""
        mock_system.llm_chat.session_manager.get_messages_for_display.return_value = []
        mock_system.llm_chat.session_manager.get_messages_for_llm.return_value = []
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.config') as mock_config:
                mock_config.CONTEXT_LIMIT = 32000
                response = client.get('/api/history', headers={'X-API-Key': 'test'})
                
                assert response.status_code == 200
                data = json.loads(response.data)
                context = data['context']
                assert 'used' in context
                assert 'limit' in context
                assert 'percent' in context


# =============================================================================
# System Status Endpoint Tests
# =============================================================================

class TestSystemStatusEndpoint:
    """Test /system/status endpoint."""
    
    def test_system_status_returns_expected_fields(self, client, mock_system):
        """GET /system/status should return all expected fields."""
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {'mode': 'monolith'}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 100
                mock_prompts.get_current_spice.return_value = None
                mock_prompts.is_assembled_mode.return_value = False
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    
                    response = client.get('/api/system/status', headers={'X-API-Key': 'test'})
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Check required fields exist
            assert 'prompt' in data
            assert 'functions' in data
            assert 'ability' in data


# =============================================================================
# Unified Status Endpoint Tests
# =============================================================================

class TestUnifiedStatusEndpoint:
    """Test /status unified endpoint."""
    
    def test_unified_status_returns_all_fields(self, client, mock_system):
        """GET /status should return all expected fields."""
        mock_system.llm_chat.get_active_chat.return_value = 'General'
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.is_streaming = False
        mock_system.llm_chat.current_system_prompt = "Test prompt"
        mock_system.tts._is_playing = False
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {'mode': 'monolith'}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 100
                mock_prompts.get_current_spice.return_value = None
                mock_prompts.is_assembled_mode.return_value = False
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    mock_config.CONTEXT_LIMIT = 32000
                    
                    response = client.get('/api/status', headers={'X-API-Key': 'test'})
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        # Check all unified status fields exist
        assert 'prompt_name' in data
        assert 'prompt_char_count' in data
        assert 'prompt' in data
        assert 'ability' in data
        assert 'functions' in data
        assert 'has_cloud_tools' in data
        assert 'tts_enabled' in data
        assert 'tts_playing' in data
        assert 'active_chat' in data
        assert 'is_streaming' in data
        assert 'message_count' in data
        assert 'spice' in data
        assert 'context' in data
    
    def test_unified_status_context_fields(self, client, mock_system):
        """GET /status context should have used, limit, percent."""
        mock_system.llm_chat.get_active_chat.return_value = 'General'
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.is_streaming = False
        mock_system.llm_chat.current_system_prompt = ""
        mock_system.tts._is_playing = False
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 0
                mock_prompts.get_current_spice.return_value = None
                mock_prompts.is_assembled_mode.return_value = False
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    mock_config.CONTEXT_LIMIT = 32000
                    
                    response = client.get('/api/status', headers={'X-API-Key': 'test'})
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        context = data['context']
        assert 'used' in context
        assert 'limit' in context
        assert 'percent' in context
        assert context['limit'] == 32000
    
    def test_unified_status_spice_fields(self, client, mock_system):
        """GET /status spice should have current, enabled, available."""
        mock_system.llm_chat.get_active_chat.return_value = 'General'
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.is_streaming = False
        mock_system.llm_chat.current_system_prompt = ""
        mock_system.tts._is_playing = False
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 0
                mock_prompts.get_current_spice.return_value = 'playful'
                mock_prompts.is_assembled_mode.return_value = True
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    mock_config.CONTEXT_LIMIT = 32000
                    
                    response = client.get('/api/status', headers={'X-API-Key': 'test'})
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        spice = data['spice']
        assert 'current' in spice
        assert 'enabled' in spice
        assert 'available' in spice
        assert spice['current'] == 'playful'
        assert spice['available'] == True
    
    def test_unified_status_message_count(self, client, mock_system):
        """GET /status should return correct message_count."""
        test_messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi!'},
            {'role': 'user', 'content': 'How are you?'},
        ]
        mock_system.llm_chat.session_manager.get_messages.return_value = test_messages
        mock_system.llm_chat.session_manager.get_messages_for_llm.return_value = test_messages
        mock_system.llm_chat.get_active_chat.return_value = 'General'
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.is_streaming = False
        mock_system.llm_chat.current_system_prompt = ""
        mock_system.tts._is_playing = False
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 0
                mock_prompts.get_current_spice.return_value = None
                mock_prompts.is_assembled_mode.return_value = False
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    mock_config.CONTEXT_LIMIT = 32000
                    
                    response = client.get('/api/status', headers={'X-API-Key': 'test'})
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['message_count'] == 3
    
    def test_unified_status_tts_playing(self, client, mock_system):
        """GET /status should return tts_playing state."""
        mock_system.llm_chat.get_active_chat.return_value = 'General'
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.is_streaming = False
        mock_system.llm_chat.current_system_prompt = ""
        mock_system.tts._is_playing = True  # TTS is playing
        
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_current_state.return_value = {}
                mock_prompts.get_active_preset_name.return_value = 'default'
                mock_prompts.get_prompt_char_count.return_value = 0
                mock_prompts.get_current_spice.return_value = None
                mock_prompts.is_assembled_mode.return_value = False
                
                with patch('core.api.config') as mock_config:
                    mock_config.TTS_ENABLED = True
                    mock_config.CONTEXT_LIMIT = 32000
                    
                    response = client.get('/api/status', headers={'X-API-Key': 'test'})
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['tts_playing'] == True


# =============================================================================
# System Prompt Endpoint Tests
# =============================================================================

class TestSystemPromptEndpoint:
    """Test /system/prompt endpoints."""
    
    def test_get_system_prompt_active(self, client, mock_system):
        """GET /system/prompt should return active prompt."""
        mock_system.llm_chat.get_system_prompt_template.return_value = "Test system prompt"
        
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.get('/api/system/prompt', headers={'X-API-Key': 'test'})
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert 'prompt' in data
    
    def test_get_system_prompt_by_name(self, client):
        """GET /system/prompt?prompt_name=X should return named prompt."""
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_prompt.return_value = {'content': 'Named prompt content'}
                
                response = client.get(
                    '/api/system/prompt?prompt_name=test_prompt',
                    headers={'X-API-Key': 'test'}
                )
                
                assert response.status_code == 200
                data = json.loads(response.data)
                assert 'prompt' in data
    
    def test_get_system_prompt_by_name_not_found(self, client):
        """GET /system/prompt with unknown name should return 404."""
        with patch('core.setup.get_password_hash', return_value="test"):
            with patch('core.api.prompts') as mock_prompts:
                mock_prompts.get_prompt.return_value = None
                
                response = client.get(
                    '/api/system/prompt?prompt_name=nonexistent',
                    headers={'X-API-Key': 'test'}
                )
                
                assert response.status_code == 404
    
    def test_set_system_prompt(self, client, mock_system):
        """POST /system/prompt should update prompt."""
        mock_system.llm_chat.set_system_prompt.return_value = True
        
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.post(
                '/api/system/prompt',
                headers={'X-API-Key': 'test', 'Content-Type': 'application/json'},
                data=json.dumps({'new_prompt': 'New test prompt'})
            )
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'success'
    
    def test_set_system_prompt_missing_body(self, client):
        """POST /system/prompt without new_prompt should return 400."""
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.post(
                '/api/system/prompt',
                headers={'X-API-Key': 'test', 'Content-Type': 'application/json'},
                data=json.dumps({})
            )
            
            assert response.status_code == 400


# =============================================================================
# Modules Endpoint Tests
# =============================================================================

class TestModulesEndpoint:
    """Test /modules endpoint."""
    
    def test_get_modules_returns_list(self, client, mock_system):
        """GET /modules should return module list."""
        mock_system.llm_chat.module_loader.get_module_list.return_value = [
            {'name': 'test_module', 'enabled': True}
        ]
        
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.get('/api/modules', headers={'X-API-Key': 'test'})
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert isinstance(data, list)


# =============================================================================
# Cancel Endpoint Tests
# =============================================================================

class TestCancelEndpoint:
    """Test /cancel endpoint."""
    
    def test_cancel_sets_flag(self, client, mock_system):
        """POST /cancel should set cancel flag."""
        mock_system.llm_chat.streaming_chat = MagicMock()
        mock_system.llm_chat.streaming_chat.cancel_flag = False
        
        with patch('core.setup.get_password_hash', return_value="test"):
            response = client.post('/api/cancel', headers={'X-API-Key': 'test'})
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'success'


# =============================================================================
# API Blueprint Tests
# =============================================================================

class TestAPIBlueprint:
    """Test API blueprint creation."""
    
    def test_create_api_returns_blueprint(self, mock_system):
        """create_api should return a Flask Blueprint."""
        from core.api import create_api
        
        bp = create_api(mock_system)
        
        from flask import Blueprint
        assert isinstance(bp, Blueprint)
    
    def test_create_api_with_callbacks(self, mock_system):
        """create_api should accept restart/shutdown callbacks."""
        from core.api import create_api
        
        restart_cb = MagicMock()
        shutdown_cb = MagicMock()
        
        bp = create_api(mock_system, restart_callback=restart_cb, shutdown_callback=shutdown_cb)
        
        assert bp is not None


# =============================================================================
# Integration Tests
# =============================================================================

class TestAPIIntegration:
    """Integration tests for API module."""
    
    def test_api_module_imports(self):
        """API module should import without errors."""
        from core.api import create_api, app
        assert create_api is not None
        assert app is not None
    
    def test_api_has_flask_app(self):
        """API module should have Flask app instance."""
        from core.api import app
        assert isinstance(app, Flask)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])