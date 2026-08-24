import asyncio

from plugins.discord.api import channels as channels_api


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self, timeout=None):
        return self._result


class _FakeTransport:
    def __init__(self, targets):
        self._targets = targets

    def list_connected(self):
        return ['alpha']

    async def list_proactive_targets(self):
        return self._targets


class _FakeRuntime:
    def __init__(self, transport):
        self.transport = transport


def test_list_proactive_targets_api(monkeypatch):
    targets = [{
        'account': 'alpha',
        'guild_id': '100',
        'guild_name': 'Test Server',
        'channel_id': '200',
        'channel_name': 'general',
        'value': 'alpha:200',
        'label': 'alpha · Test Server · #general',
    }]
    runtime = _FakeRuntime(_FakeTransport(targets))
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: runtime)
    monkeypatch.setattr(channels_api, 'run_coroutine', lambda coro: _FakeFuture(targets))

    result = asyncio.run(channels_api.list_proactive_targets())

    assert result['connected'] is True
    assert result['targets'][0]['value'] == 'alpha:200'


def test_list_proactive_targets_voice_query(monkeypatch):
    targets = [{
        'account': 'alpha',
        'guild_id': '100',
        'guild_name': 'Test Server',
        'channel_id': '300',
        'channel_name': 'Lounge',
        'member_count': 2,
        'value': 'alpha:300',
        'label': 'alpha · Test Server · Lounge (2 in channel)',
    }]

    class _VoiceTransport(_FakeTransport):
        async def list_voice_targets(self):
            return self._targets

    runtime = _FakeRuntime(_VoiceTransport(targets))
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: runtime)
    monkeypatch.setattr(channels_api, 'run_coroutine', lambda coro: _FakeFuture(targets))

    result = asyncio.run(channels_api.list_proactive_targets(query={'channel_type': 'voice'}))

    assert result['connected'] is True
    assert result['channel_type'] == 'voice'
    assert result['targets'][0]['value'] == 'alpha:300'


def test_list_proactive_targets_api_offline(monkeypatch):
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: None)

    result = asyncio.run(channels_api.list_proactive_targets())

    assert result['connected'] is False
    assert result['targets'] == []


def test_list_voice_targets_api(monkeypatch):
    targets = [{
        'account': 'alpha',
        'guild_id': '100',
        'guild_name': 'Test Server',
        'channel_id': '300',
        'channel_name': 'Lounge',
        'member_count': 2,
        'value': 'alpha:300',
        'label': 'alpha · Test Server · Lounge (2 in channel)',
    }]

    class _VoiceTransport(_FakeTransport):
        async def list_voice_targets(self):
            return self._targets

    runtime = _FakeRuntime(_VoiceTransport(targets))
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: runtime)
    monkeypatch.setattr(channels_api, 'run_coroutine', lambda coro: _FakeFuture(targets))

    result = asyncio.run(channels_api.list_voice_targets())

    assert result['connected'] is True
    assert result['targets'][0]['value'] == 'alpha:300'
    assert result['targets'][0]['member_count'] == 2


def test_list_voice_targets_api_offline(monkeypatch):
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: None)

    result = asyncio.run(channels_api.list_voice_targets())

    assert result['connected'] is False
    assert result['targets'] == []


def test_list_bot_allowlist_api(monkeypatch):
    bots = [{
        'account': 'remmi',
        'user_id': '500',
        'username': 'Sapphire',
        'display_name': 'Sapphire',
        'value': '500',
        'label': 'Sapphire (@Sapphire) · Test Server',
    }]

    class _BotTransport(_FakeTransport):
        async def list_guild_bots(self):
            return bots

    runtime = _FakeRuntime(_BotTransport([]))
    monkeypatch.setattr(channels_api, 'get_runtime', lambda: runtime)
    monkeypatch.setattr(channels_api, 'run_coroutine', lambda coro: _FakeFuture(bots))

    result = asyncio.run(channels_api.list_bot_allowlist_candidates())

    assert result['connected'] is True
    assert result['bots'][0]['value'] == '500'
