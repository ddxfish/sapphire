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
