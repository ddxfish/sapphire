"""Broadsword hunt fix wave — Discord rows H4, H5, H6, H7 (2026-09-18).
Record: tmp/broadsword-hunt-20260918.md."""
import asyncio
from pathlib import Path

from plugins.discord.models.settings import VoiceSettings, SettingsOverlay
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
from plugins.discord.vision.vision_bridge import VisionBridge

PLUGIN = Path(__file__).resolve().parent.parent


def _src(rel):
    return (PLUGIN / rel).read_text(encoding="utf-8")


# ── H4: voice.enabled is the one switch, and off means LEAVE ───────────────

class _Session:
    def __init__(self, channel_id):
        self.channel_id = channel_id


class _Repo:
    def __init__(self, channels):
        self._channels = channels

    def list_active_sessions(self, account_name):
        return [_Session(c) for c in self._channels]


class _SessionService:
    def __init__(self, channels):
        self.voice_session_repository = _Repo(channels)


class _Voice:
    def __init__(self, channels):
        self.voice_session_service = _SessionService(channels)
        self.leaves = []

    def leave(self, intention):
        self.leaves.append(intention)
        return {'status': 'left', 'channel_id': intention.channel_id}

    async def leave_async(self, intention):
        return self.leave(intention)


class _Settings:
    def __init__(self, enabled):
        self.voice = type('V', (), {'enabled': enabled, 'join_targets': ['alpha:vc1']})()


class _Store:
    def __init__(self, settings):
        self._s = settings

    def resolve(self, **_kw):
        return self._s


def test_h4_voice_off_leaves_every_live_channel_sync_and_async():
    voice = _Voice(['vc1', 'vc2'])
    svc = VoiceAutoJoinService(transport=object(), voice_service=voice,
                               settings_store=_Store(_Settings(enabled=False)))
    out = svc.tick('alpha')
    assert [i.channel_id for i in voice.leaves] == ['vc1', 'vc2']
    assert all(i.reason == 'voice_disabled' for i in voice.leaves)
    assert len(out) == 2

    voice2 = _Voice(['vc7'])
    svc2 = VoiceAutoJoinService(transport=object(), voice_service=voice2,
                                settings_store=_Store(_Settings(enabled=False)))
    asyncio.run(svc2.tick_async('alpha'))
    assert [i.channel_id for i in voice2.leaves] == ['vc7']


def test_h4_voice_off_with_nothing_live_is_a_quiet_noop():
    voice = _Voice([])
    svc = VoiceAutoJoinService(transport=object(), voice_service=voice,
                               settings_store=_Store(_Settings(enabled=False)))
    assert svc.tick('alpha') == []
    assert voice.leaves == []


def test_h4_emergency_switch_is_gone_and_old_stores_still_load():
    assert not hasattr(VoiceSettings(), 'emergency_disabled')
    assert 'emergency_disabled' not in _src('plugin.json')
    for rel in ('voice/auto_join_service.py', 'voice/voice_service.py', 'voice/voice_listener_service.py',
                'voice/voice_conversation_service.py', 'cognition/policy_service.py'):
        assert 'emergency_disabled' not in _src(rel), rel
    # a settings.json written before the removal still loads — unknown keys drop
    overlay = SettingsOverlay.from_dict({'voice': {'enabled': True, 'emergency_disabled': True}})
    assert overlay is not None


# ── H5: the payload lane reads the caption lane's bytes, never fetches ─────

def test_h5_vision_bridge_caches_fetched_bytes_and_cached_bytes_never_fetches():
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return b'PNGDATA', 'image/png'

    bridge = VisionBridge(fetch_bytes=fake_fetch)
    assert bridge.fetch_bytes('https://cdn/x.png') == (b'PNGDATA', 'image/png')
    assert bridge.fetch_bytes('https://cdn/x.png') == (b'PNGDATA', 'image/png')
    assert calls == ['https://cdn/x.png']
    assert bridge.cached_bytes('https://cdn/x.png') == (b'PNGDATA', 'image/png')
    assert bridge.cached_bytes('https://cdn/never.png') is None
    assert calls == ['https://cdn/x.png'], 'cached_bytes must not fetch'
    for i in range(12):
        bridge.fetch_bytes(f'https://cdn/{i}.png')
    assert len(bridge._bytes_cache) == 8


def test_h5_payload_images_use_the_cache_only():
    src = _src('conversation/conversation_service.py')
    body = src[src.index('def _payload_images('):src.index('def _dm_within_budget(')]
    assert "getattr(bridge, 'cached_bytes', None)" in body
    assert "getattr(bridge, 'fetch_bytes', None)" not in body


# ── H6: ignored channels are dropped before anything observes them ─────────

def test_h6_ignore_check_runs_before_observation():
    src = _src('transport/discord_event_adapter.py')
    body = src[src.index('def adapt_message_event('):]
    assert body.index('is_channel_ignored(') < body.index('record_text_observation(')
    assert body.index('is_channel_ignored(') < body.index('scan_and_schedule(')
    assert body.count("record_trace('event_dropped', 'Ignored channel'") == 1


def test_h6_task_follow_up_respects_the_ignore_list():
    src = _src('cognition/cognitive_orchestrator.py')
    body = src[src.index('def _generate_task_follow_up('):]
    assert 'is_channel_ignored(account_name, channel_id, settings)' in body


# ── H7: a voice session summary is a record, not a scheduled post ──────────

def test_h7_summarize_session_schedules_nothing():
    src = _src('voice/voice_session_service.py')
    body = src[src.index('def summarize_session('):]
    assert 'create_task(' not in body
    assert "'voice_follow_up'" not in body
