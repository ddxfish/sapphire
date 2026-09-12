"""Slash command registration and handler behavior."""

import asyncio

from plugins.discord.transport import discord_slash


class FakeGroup:
    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.commands = {}

    def command(self, name='', description=''):
        def decorator(fn):
            self.commands[name] = fn
            return fn
        return decorator


class FakeBot:
    def __init__(self):
        self.groups = []

    def create_group(self, name, description=''):
        group = FakeGroup(name, description)
        self.groups.append(group)
        return group


class FakeCtx:
    def __init__(self, author_channel=None, guild_id='g1'):
        self.responses = []
        self.deferred = False
        self.guild = type('G', (), {'id': guild_id})()
        voice = type('V', (), {'channel': author_channel})() if author_channel else None
        self.author = type('A', (), {'voice': voice})()

    async def respond(self, message, **kwargs):
        self.responses.append(message)

    async def defer(self):
        self.deferred = True


class FakeChannel:
    def __init__(self, channel_id='555', name='Voice Chat 1', guild_id='g1'):
        self.id = channel_id
        self.name = name
        self.guild = type('G', (), {'id': guild_id})()


class FakeVoiceService:
    def __init__(self, join_status='joined'):
        self.join_status = join_status
        self.joins = []
        self.leaves = []

    async def join_async(self, intention):
        self.joins.append(intention)
        if self.join_status == 'joined':
            return {'status': 'joined'}
        return {'status': 'blocked', 'reason': 'voice_disabled'}

    async def leave_async(self, intention):
        self.leaves.append(intention)
        return {'status': 'left'}


class FakeVoiceTransport:
    def __init__(self, connections=None):
        self._rows = connections or []

    def list_connections(self, account_name=None):
        return list(self._rows)


class FakeRuntime:
    def __init__(self, join_status='joined', connections=None):
        self.voice_service = FakeVoiceService(join_status)
        self.voice_transport = FakeVoiceTransport(connections)


def test_register_skips_plain_clients():
    assert discord_slash.register_slash_commands(object(), 'alpha') is False


def test_register_attaches_voice_group():
    bot = FakeBot()
    assert discord_slash.register_slash_commands(bot, 'alpha') is True
    assert bot.groups[0].name == 'voice'
    assert set(bot.groups[0].commands) == {'join', 'leave'}


def test_voice_join_defaults_to_author_channel(monkeypatch):
    bot = FakeBot()
    discord_slash.register_slash_commands(bot, 'alpha')
    runtime = FakeRuntime()
    monkeypatch.setattr(discord_slash, '_runtime', lambda: runtime)
    ctx = FakeCtx(author_channel=FakeChannel())

    asyncio.run(bot.groups[0].commands['join'](ctx, None))

    assert ctx.deferred is True
    intention = runtime.voice_service.joins[0]
    assert intention.account_name == 'alpha'
    assert intention.channel_id == '555'
    assert intention.guild_id == 'g1'
    assert 'Voice Chat 1' in ctx.responses[0]


def test_voice_join_without_channel_prompts(monkeypatch):
    bot = FakeBot()
    discord_slash.register_slash_commands(bot, 'alpha')
    runtime = FakeRuntime()
    monkeypatch.setattr(discord_slash, '_runtime', lambda: runtime)
    ctx = FakeCtx(author_channel=None)

    asyncio.run(bot.groups[0].commands['join'](ctx, None))

    assert runtime.voice_service.joins == []
    assert 'join a voice channel first' in ctx.responses[0].lower()


def test_voice_join_blocked_reports_settings(monkeypatch):
    bot = FakeBot()
    discord_slash.register_slash_commands(bot, 'alpha')
    runtime = FakeRuntime(join_status='blocked')
    monkeypatch.setattr(discord_slash, '_runtime', lambda: runtime)
    ctx = FakeCtx(author_channel=FakeChannel())

    asyncio.run(bot.groups[0].commands['join'](ctx, None))

    assert 'disabled' in ctx.responses[0].lower()


def test_voice_leave_disconnects_guild_connections(monkeypatch):
    bot = FakeBot()
    discord_slash.register_slash_commands(bot, 'alpha')
    runtime = FakeRuntime(connections=[
        {'channel_id': '555', 'guild_id': 'g1'},
        {'channel_id': '777', 'guild_id': 'g2'},
    ])
    monkeypatch.setattr(discord_slash, '_runtime', lambda: runtime)
    ctx = FakeCtx(guild_id='g1')

    asyncio.run(bot.groups[0].commands['leave'](ctx))

    assert [i.channel_id for i in runtime.voice_service.leaves] == ['555']
    assert 'left voice' in ctx.responses[0].lower()


def test_voice_leave_not_connected(monkeypatch):
    bot = FakeBot()
    discord_slash.register_slash_commands(bot, 'alpha')
    runtime = FakeRuntime(connections=[])
    monkeypatch.setattr(discord_slash, '_runtime', lambda: runtime)
    ctx = FakeCtx(guild_id='g1')

    asyncio.run(bot.groups[0].commands['leave'](ctx))

    assert runtime.voice_service.leaves == []
    assert 'not connected' in ctx.responses[0].lower()
