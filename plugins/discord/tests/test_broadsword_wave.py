"""Broadsword hunt fix wave — Discord rows H4, H5, H6, H7 (2026-09-18).
Record: tmp/broadsword-hunt-20260918.md."""
import asyncio
from pathlib import Path

from plugins.discord.models.settings import VoiceSettings, SettingsOverlay
from plugins.discord.voice.auto_join_service import VoiceAutoJoinService
from plugins.discord.conversation.images import ImageLane

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

    def list_active(self, account_name):
        return [_Session(c) for c in self._channels]


class _Voice:
    def __init__(self, channels):
        self.sessions = _Repo(channels)
        self.leaves = []

    def leave(self, intention):
        self.leaves.append(intention)
        return {'status': 'left', 'channel_id': intention.channel_id}

    async def leave_async(self, intention):
        return self.leave(intention)


class _NoGate:
    def tasks(self, account_name):
        return []

    def allowed(self, account_name, channel_id):
        return None


def test_h4_voice_off_leaves_every_live_channel_sync_and_async():
    # S6: "voice off" = no enabled Discord: Voice channel task for the bot.
    voice = _Voice(['vc1', 'vc2'])
    svc = VoiceAutoJoinService(transport=object(), voice_service=voice, gate=_NoGate())
    out = asyncio.run(svc.tick_async('alpha'))
    assert [i.channel_id for i in voice.leaves] == ['vc1', 'vc2']
    assert all(i.reason == 'no_voice_task' for i in voice.leaves)
    assert len(out) == 2

    voice2 = _Voice(['vc7'])
    svc2 = VoiceAutoJoinService(transport=object(), voice_service=voice2, gate=_NoGate())
    asyncio.run(svc2.tick_async('alpha'))
    assert [i.channel_id for i in voice2.leaves] == ['vc7']


def test_h4_voice_off_with_nothing_live_is_a_quiet_noop():
    voice = _Voice([])
    svc = VoiceAutoJoinService(transport=object(), voice_service=voice, gate=_NoGate())
    assert asyncio.run(svc.tick_async('alpha')) == []
    assert voice.leaves == []


def test_h4_emergency_switch_is_gone_and_old_stores_still_load():
    assert not hasattr(VoiceSettings(), 'emergency_disabled') and not hasattr(VoiceSettings(), 'enabled')
    assert 'emergency_disabled' not in _src('plugin.json')
    for rel in ('voice/auto_join_service.py', 'voice/voice_service.py', 'voice/voice_listener_service.py',
                'conversation/policy_service.py'):
        assert 'emergency_disabled' not in _src(rel), rel
    # a settings.json written before the removal still loads — unknown keys drop
    overlay = SettingsOverlay.from_dict({'voice': {'enabled': True, 'emergency_disabled': True}})
    assert overlay is not None


# ── H5: the payload lane reads the caption lane's bytes, never fetches ─────

def test_h5_payload_lane_reads_the_cache_and_never_fetches():
    from types import SimpleNamespace
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return b'PNGDATA', 'image/png'

    lane = ImageLane(fetch=fake_fetch)
    settings = SimpleNamespace(media=SimpleNamespace(images_in_enabled=True, max_images=4))
    obs = SimpleNamespace(attachments=[{'url': 'https://cdn/x.png', 'content_type': 'image/png', 'filename': 'x.png'},
                                       {'url': 'https://cdn/never.png', 'content_type': 'image/png', 'filename': 'n.png'}])
    assert lane.payload_images(obs, settings) == []                 # nothing cached → nothing, no fetch
    assert calls == []
    assert lane.fetch(SimpleNamespace(attachments=obs.attachments[:1]), settings) == 1
    assert lane.fetch(SimpleNamespace(attachments=obs.attachments[:1]), settings) == 1   # cached: no refetch
    assert calls == ['https://cdn/x.png']
    out = lane.payload_images(obs, settings)
    assert len(out) == 1 and out[0]['media_type'] == 'image/png' and calls == ['https://cdn/x.png']
    src = _src('conversation/conversation_service.py')
    body = src[src.index('def _payload_images('):src.index('def _dm_within_budget(')]
    assert 'payload_images(' in body and 'fetch' not in body


# ── H6: ignored channels are dropped before anything observes them ─────────

def test_h6_ignore_check_runs_before_observation():
    src = _src('transport/discord_event_adapter.py')
    body = src[src.index('def adapt_message_event('):]
    assert body.index('is_channel_ignored(') < body.index('save_message(')
    assert body.count("record_trace('event_dropped', 'Ignored channel'") == 1


