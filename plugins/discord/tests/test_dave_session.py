import asyncio

from plugins.discord.voice.dave_session import (
    voice_dave_snapshot,
    wait_for_dave_ready,
)


class FakeDave:
    def __init__(self, *, ready=False, status='pending'):
        self.ready = ready
        self.status = status
        self.passthrough_calls = 0

    def set_passthrough_mode(self, enabled, duration=0):
        self.passthrough_calls += 1
        self.passthrough_enabled = enabled
        self.passthrough_duration = duration


class FakeConnection:
    def __init__(self, dave=None):
        self.dave_session = dave


class FakeChannel:
    id = 999


class FakeVoiceClient:
    def __init__(self, *, dave=None, connected=True):
        self._connection = FakeConnection(dave)
        self.channel = FakeChannel()
        self._connected = connected

    def is_connected(self):
        return self._connected

    def is_dave_connection(self):
        return self._connection.dave_session is not None


def test_snapshot_non_dave():
    client = FakeVoiceClient()
    snap = voice_dave_snapshot(client)
    assert snap['is_dave'] is False
    assert snap['dave_ready'] is False


def test_snapshot_dave_ready():
    client = FakeVoiceClient(dave=FakeDave(ready=True, status='ready'))
    snap = voice_dave_snapshot(client)
    assert snap['is_dave'] is True
    assert snap['dave_ready'] is True


def test_wait_for_dave_ready_returns_immediately_when_ready():
    dave = FakeDave(ready=True)
    client = FakeVoiceClient(dave=dave)
    snap = asyncio.run(wait_for_dave_ready(client, timeout=1.0))
    assert snap['dave_ready'] is True
    assert snap.get('passthrough_enabled') is True
    assert dave.passthrough_calls == 1
    assert 'wait_timed_out' not in snap


def test_wait_for_dave_ready_times_out():
    client = FakeVoiceClient(dave=FakeDave(ready=False, status='handshake'))
    snap = asyncio.run(wait_for_dave_ready(client, timeout=0.6))
    assert snap['wait_timed_out'] is True
