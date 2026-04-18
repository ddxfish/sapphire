"""Surface 2 P2 — /api/status route shape + readiness flags.

Covers:
  2.30 /api/status reflects chat settings after PUT (chat_settings key)
  2.50 /api/status private_chat bit
  status: tts_playing / stt_ready / wakeword_ready / is_streaming
  status: context percent clamped

/api/status is the UI's per-poll sync point — regressions here = stale UI.

See tmp/coverage-test-plan.md Surface 2 P2.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def status_client(client, mock_system, monkeypatch):
    """TestClient + mock_system configured with enough stubs for /api/status
    to return 200 without exploding on a thousand attribute misses."""
    c, csrf = client
    sm = mock_system.llm_chat.session_manager
    sm.get_chat_settings.return_value = {
        'trim_color': '#abcdef',
        'persona': 'sapphire',
        'private_chat': False,
    }
    sm.get_messages.return_value = []
    mock_system.llm_chat.get_active_chat.return_value = 'trinity'
    mock_system.llm_chat.list_chats.return_value = [{'name': 'trinity'}]

    fm = mock_system.llm_chat.function_manager
    fm.get_enabled_function_names.return_value = ['tool_a']
    fm.get_current_toolset_info.return_value = {'name': 'default', 'function_count': 1}
    fm.has_network_tools_enabled.return_value = False

    sc = mock_system.llm_chat.streaming_chat
    sc.is_streaming = False

    # TTS
    mock_system.tts._is_playing = False

    # Detectors — use real null instances so isinstance checks work
    from core.wakeword.wakeword_null import NullWakeWordDetector
    from core.stt.stt_null import NullWhisperClient
    mock_system.wake_detector = NullWakeWordDetector('dummy_model_path')
    mock_system.whisper_client = NullWhisperClient()

    mock_system.llm_chat.current_system_prompt = 'You are test.'

    # config knobs
    import config
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 32000, raising=False)
    monkeypatch.setattr(config, 'TTS_ENABLED', False, raising=False)
    monkeypatch.setattr(config, 'TTS_PROVIDER', 'none', raising=False)
    monkeypatch.setattr(config, 'STT_ENABLED', False, raising=False)
    monkeypatch.setattr(config, 'STT_PROVIDER', 'none', raising=False)
    monkeypatch.setattr(config, 'WAKE_WORD_ENABLED', False, raising=False)

    return c, csrf, mock_system


# ─── /api/status baseline shape ──────────────────────────────────────────────

def test_status_returns_all_expected_keys(status_client):
    """The frontend polls /api/status and assumes every key exists. Any
    missing key = JS undefined crash in the UI poll loop."""
    c, csrf, sys_mock = status_client
    r = c.get('/api/status')
    assert r.status_code == 200, r.text
    body = r.json()
    required_keys = [
        'toolset', 'functions', 'state_tools', 'has_cloud_tools',
        'tts_enabled', 'tts_provider', 'stt_enabled', 'stt_provider',
        'stt_ready', 'wakeword_enabled', 'wakeword_ready',
        'tts_playing', 'active_chat', 'is_streaming', 'message_count',
        'spice', 'context', 'chats', 'chat_settings',
    ]
    for k in required_keys:
        assert k in body, f"missing key in /api/status: {k}"


# ─── 2.30 status reflects chat_settings.private_chat ─────────────────────────

def test_status_includes_chat_settings_private_flag(status_client):
    """[PROACTIVE] `chat_settings.private_chat=True` must appear in /api/status
    so UI can render the private-chat badge. Guards against the flag being
    stripped in serialization."""
    c, csrf, sys_mock = status_client
    sys_mock.llm_chat.session_manager.get_chat_settings.return_value = {
        'private_chat': True, 'persona': 'journal',
    }
    r = c.get('/api/status')
    assert r.status_code == 200
    body = r.json()
    assert body['chat_settings']['private_chat'] is True


def test_status_stt_ready_false_when_null_client(status_client):
    """stt_ready is derived from isinstance check — NullSTTClient → False."""
    c, csrf, sys_mock = status_client
    r = c.get('/api/status')
    assert r.status_code == 200
    assert r.json()['stt_ready'] is False


def test_status_stt_ready_true_when_real_client(status_client):
    """Inverse: any non-null client → stt_ready=True."""
    c, csrf, sys_mock = status_client
    # Swap in a non-null mock (any object that's not NullSTTClient qualifies)
    sys_mock.whisper_client = MagicMock(name='RealWhisperClient')
    r = c.get('/api/status')
    assert r.status_code == 200
    assert r.json()['stt_ready'] is True


def test_status_wakeword_ready_false_when_null_detector(status_client):
    c, csrf, sys_mock = status_client
    r = c.get('/api/status')
    assert r.status_code == 200
    assert r.json()['wakeword_ready'] is False


def test_status_tts_playing_reflects_flag(status_client):
    """tts._is_playing=True surfaces as tts_playing=True in status."""
    c, csrf, sys_mock = status_client
    sys_mock.tts._is_playing = True
    r = c.get('/api/status')
    assert r.status_code == 200
    assert r.json()['tts_playing'] is True


def test_status_context_percent_clamped_to_100(status_client, monkeypatch):
    """If history_tokens > context_limit (edge case with huge history),
    context.percent must clamp at 100 — not overflow past. Protects the
    UI progress bar."""
    c, csrf, sys_mock = status_client
    # Stuff history with content that will token-count over the limit
    huge = {'role': 'user', 'content': 'word ' * 20000}
    sys_mock.llm_chat.session_manager.get_messages.return_value = [huge]
    import config
    monkeypatch.setattr(config, 'CONTEXT_LIMIT', 100, raising=False)

    r = c.get('/api/status')
    assert r.status_code == 200
    pct = r.json()['context']['percent']
    assert pct == 100, f"expected clamp at 100, got {pct}"


def test_status_active_chat_matches_system(status_client):
    c, csrf, sys_mock = status_client
    sys_mock.llm_chat.get_active_chat.return_value = 'my_special_chat'
    r = c.get('/api/status')
    assert r.status_code == 200
    assert r.json()['active_chat'] == 'my_special_chat'
