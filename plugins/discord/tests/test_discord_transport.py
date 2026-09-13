import asyncio
import logging

from plugins.discord.transport.discord_transport import DiscordTransport

logger = logging.getLogger(__name__)


class FakeUser:
    name = "Alpha"
    id = 42


class FakeClient:
    def __init__(self, *, intents=None):
        self.intents = intents
        self.user = FakeUser()
        self.closed = False

    def event(self, func):
        if func.__name__ == "on_ready":
            self._on_ready = func
        return func

    async def start(self, token):
        self.token = token
        await self._on_ready()
        while not self.closed:
            await asyncio.sleep(0)

    async def close(self):
        self.closed = True


class FakeChannel:
    def __init__(self, channel_id: int, name: str, *, members=None):
        self.id = channel_id
        self.name = name
        self.members = members or []


class FakeVoiceChannel(FakeChannel):
    type = 2


class FakeGuild:
    def __init__(self, guild_id: int, name: str, channels, *, voice_channels=None, members=None, member_count=None):
        self.id = guild_id
        self.name = name
        self.text_channels = [c for c in channels if not isinstance(c, FakeVoiceChannel) and getattr(c, 'type', 0) != 2]
        self.voice_channels = voice_channels if voice_channels is not None else [
            c for c in channels if isinstance(c, FakeVoiceChannel) or getattr(c, 'type', 0) == 2
        ]
        self.channels = channels
        self.members = members or []
        self.member_count = member_count if member_count is not None else len(self.members)

    async def chunk(self):
        return None


class FakeMember:
    def __init__(self, user_id: int, name: str, *, bot: bool = True, display_name: str | None = None):
        self.id = user_id
        self.name = name
        self.display_name = display_name or name
        self.bot = bot


class FakeClientWithGuilds(FakeClient):
    def __init__(self, *, intents=None, guilds=None):
        super().__init__(intents=intents)
        self.guilds = guilds or []


def test_list_proactive_targets_from_connected_guilds(tmp_path):
    async def run_test():
        guild = FakeGuild(100, 'Test Server', [FakeChannel(200, 'general'), FakeChannel(201, 'random')])
        transport = DiscordTransport(
            loop=asyncio.get_running_loop(),
            client_factory=lambda **kwargs: FakeClientWithGuilds(guilds=[guild]),
        )
        await transport.connect_account('alpha', 'secret')
        await asyncio.sleep(0)
        targets = await transport.list_proactive_targets()

        assert len(targets) == 2
        assert targets[0]['value'] == 'alpha:200'
        assert targets[0]['label'] == 'alpha · Test Server · #general'

    asyncio.run(run_test())


def test_list_voice_targets_from_connected_guilds(tmp_path):
    async def run_test():
        guild = FakeGuild(100, 'Test Server', [
            FakeVoiceChannel(300, 'Lounge', members=[object(), object()]),
            FakeVoiceChannel(301, 'AFK', members=[]),
        ])
        transport = DiscordTransport(
            loop=asyncio.get_running_loop(),
            client_factory=lambda **kwargs: FakeClientWithGuilds(guilds=[guild]),
        )
        await transport.connect_account('alpha', 'secret')
        await asyncio.sleep(0)
        targets = await transport.list_voice_targets()

        assert len(targets) == 2
        values = {target['value'] for target in targets}
        assert values == {'alpha:300', 'alpha:301'}
        lounge = next(target for target in targets if target['channel_id'] == '300')
        assert lounge['member_count'] == 2
        assert 'Lounge (2 in channel)' in lounge['label']

    asyncio.run(run_test())


def test_list_guild_bots_excludes_self_and_humans(tmp_path):
    async def run_test():
        members = [
            FakeMember(500, 'Sapphire'),
            FakeMember(501, 'speedyboi'),
            FakeMember(900, 'alice', bot=False),
        ]
        guild = FakeGuild(100, 'Test Server', [FakeChannel(200, 'general')], members=members)
        transport = DiscordTransport(
            loop=asyncio.get_running_loop(),
            client_factory=lambda **kwargs: FakeClientWithGuilds(guilds=[guild]),
        )
        await transport.connect_account('remmi', 'secret')
        await asyncio.sleep(0)
        transport._accounts['remmi']['bot_id'] = '42'
        bots = await transport.list_guild_bots()

        assert len(bots) == 2
        assert {bot['value'] for bot in bots} == {'500', '501'}
        assert any('Sapphire' in bot['label'] for bot in bots)

    asyncio.run(run_test())


class SessionClosedClient(FakeClient):
    async def start(self, token):
        self.token = token
        await self._on_ready()
        while not self.closed:
            await asyncio.sleep(0)
        raise RuntimeError('Session is closed')


def test_disconnect_session_closed_is_not_logged_as_error(caplog):
    async def run_test():
        transport = DiscordTransport(loop=asyncio.get_running_loop(), client_factory=SessionClosedClient)
        await transport.connect_account('alpha', 'secret')
        await asyncio.sleep(0)

        with caplog.at_level(logging.DEBUG, logger='plugins.discord.transport.discord_transport'):
            await transport.disconnect_account('alpha')

        assert transport.account_health('alpha')['state'] == 'disconnected'
        assert not any(
            record.levelno >= logging.ERROR and 'Discord connection failed' in record.message
            for record in caplog.records
        )

    asyncio.run(run_test())


def test_transport_connect_disconnect_and_health(tmp_path):
    async def run_test():
        transport = DiscordTransport(loop=asyncio.get_running_loop(), client_factory=FakeClient)
        await transport.connect_account("alpha", "secret")
        await asyncio.sleep(0)
        health = transport.account_health("alpha")
        assert health["state"] == "connected"
        assert health["bot_name"] == "Alpha"
        assert transport.list_connected() == ["alpha"]

        await transport.disconnect_account("alpha")
        assert transport.account_health("alpha")["state"] == "disconnected"

    asyncio.run(run_test())


def test_real_client_disables_mass_mentions(monkeypatch):
    # H2 (hunt 2026-09-12): nothing she posts may ping @everyone/@here or a role.
    import sys
    import types

    calls = {}
    fake = types.ModuleType('discord')

    class Intents:
        @staticmethod
        def default():
            return types.SimpleNamespace()

    class AllowedMentions:
        def __init__(self, **kw):
            self.kw = kw

    class Bot:
        def __init__(self, **kw):
            calls.update(kw)

    fake.Intents, fake.AllowedMentions, fake.Bot = Intents, AllowedMentions, Bot
    monkeypatch.setitem(sys.modules, 'discord', fake)

    loop = asyncio.new_event_loop()
    try:
        DiscordTransport(loop=loop, client_factory=None)._make_client()
    finally:
        loop.close()
    mentions = calls['allowed_mentions'].kw
    assert mentions['everyone'] is False
    assert mentions['roles'] is False
    assert mentions['users'] is True


def test_execution_read_messages_returns_oldest_first():
    # C4 (hunt 2026-09-12): read_messages was a stub returning [].
    import datetime as dt
    import types
    from plugins.discord.transport.discord_execution import DiscordExecution

    def _msg(mid, who, text, minute):
        return types.SimpleNamespace(
            id=mid,
            author=types.SimpleNamespace(id=7, display_name=who, name=who.lower()),
            clean_content=text,
            content=text,
            created_at=dt.datetime(2026, 9, 13, 13, minute),
            attachments=[],
        )

    class FakeChannel:
        id = 123

        async def history(self, limit=20):
            for m in (_msg(3, 'Alice', 'third', 3), _msg(2, 'Bob', 'second', 2), _msg(1, 'Alice', 'first', 1))[:limit]:
                yield m

    channel = FakeChannel()
    client = types.SimpleNamespace(get_channel=lambda cid: channel if cid == 123 else None)
    execution = DiscordExecution(transport=None)
    execution._state_for_account = lambda name: ('alpha', {'client': client})

    rows = asyncio.run(execution.read_messages('123', count=3, account_name='alpha'))

    assert [r['message_id'] for r in rows] == ['1', '2', '3']
    assert rows[0]['author'] == 'Alice'
    assert rows[1]['content'] == 'second'
    assert rows[0]['created_at'] == '2026-09-13T13:01'
