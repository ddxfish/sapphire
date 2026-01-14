"""Shared pytest fixtures for Sapphire tests."""
import sys
import json
from pathlib import Path

# Add project root to path BEFORE any other imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.fixture
def settings_defaults():
    """Minimal settings defaults for testing."""
    return {
        "identity": {
            "DEFAULT_USERNAME": "TestUser",
            "DEFAULT_AI_NAME": "TestAI"
        },
        "features": {
            "MODULES_ENABLED": True,
            "PLUGINS_ENABLED": False
        },
        "llm": {
            "LLM_MAX_HISTORY": 10,
            "CONTEXT_LIMIT": 4000,
            "LLM_PRIMARY": {
                "base_url": "http://test:1234",
                "enabled": True
            }
        },
        "wakeword": {
            "RECORDER_PREFERRED_DEVICES": ["default"],
            "RECORDER_PREFERRED_DEVICES_LINUX": ["pipewire", "pulse", "default"],
            "RECORDER_PREFERRED_DEVICES_WINDOWS": ["default", "speakers"]
        }
    }


@pytest.fixture
def settings_defaults_file(tmp_path, settings_defaults):
    """Create a temporary settings_defaults.json file."""
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    defaults_file = core_dir / "settings_defaults.json"
    defaults_file.write_text(json.dumps(settings_defaults), encoding='utf-8')
    return defaults_file


@pytest.fixture
def user_settings_file(tmp_path):
    """Create a temporary user settings.json file."""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    settings_file = user_dir / "settings.json"
    settings_file.write_text('{}', encoding='utf-8')
    return settings_file


@pytest.fixture
def sample_messages():
    """Sample conversation messages for history tests."""
    return [
        {"role": "user", "content": "Hello", "timestamp": "2025-01-01T10:00:00"},
        {"role": "assistant", "content": "Hi there!", "timestamp": "2025-01-01T10:00:01"},
        {"role": "user", "content": "How are you?", "timestamp": "2025-01-01T10:00:02"},
        {"role": "assistant", "content": "I'm doing well!", "timestamp": "2025-01-01T10:00:03"},
    ]


@pytest.fixture
def sample_tool_messages():
    """Messages with tool calls for history tests."""
    return [
        {"role": "user", "content": "Search for cats", "timestamp": "2025-01-01T11:00:00"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "cats"}'}}],
            "timestamp": "2025-01-01T11:00:01"
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "name": "web_search",
            "content": "Found 10 results about cats",
            "timestamp": "2025-01-01T11:00:02"
        },
        {"role": "assistant", "content": "I found info about cats!", "timestamp": "2025-01-01T11:00:03"},
    ]


@pytest.fixture
def prompts_dir(tmp_path):
    """Create a temporary prompts directory with sample files."""
    prompts_path = tmp_path / "user" / "prompts"
    prompts_path.mkdir(parents=True)
    
    # Create sample prompt files
    pieces = {
        "components": {
            "persona": {"default": "You are a helpful AI assistant."},
            "goals": {"helpful": "Be helpful and informative."},
            "location": {},
            "relationship": {},
            "format": {},
            "scenario": {},
            "extras": {},
            "emotions": {}
        },
        "scenario_presets": {}
    }
    (prompts_path / "prompt_pieces.json").write_text(
        json.dumps(pieces), encoding='utf-8'
    )
    
    monoliths = {
        "_comment": "Test monoliths",
        "default": "You are a helpful AI assistant named Sapphire."
    }
    (prompts_path / "prompt_monoliths.json").write_text(
        json.dumps(monoliths), encoding='utf-8'
    )
    
    spices = {
        "_comment": "Test spices",
        "humor": ["Be witty", "Use puns"]
    }
    (prompts_path / "prompt_spices.json").write_text(
        json.dumps(spices), encoding='utf-8'
    )
    
    return prompts_path


@pytest.fixture
def unicode_content():
    """Sample unicode content for encoding tests."""
    return {
        "japanese": "日本語テスト",
        "chinese": "中文测试",
        "korean": "한국어 테스트",
        "emoji": "Hello 👋 World 🌍",
        "mixed": "Test テスト 测试 🎉"
    }


@pytest.fixture
def mock_bcrypt_hash():
    """A valid bcrypt hash for testing (password: 'testpass')."""
    return '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.kPQCHLxNKUQIMe'