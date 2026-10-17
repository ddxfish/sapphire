import asyncio
import json
from types import SimpleNamespace

from plugins.discord.models.observations import TextMessageObservation, TypingObservation
from plugins.discord.transport.discord_event_adapter import DiscordEventAdapter


class FakeAuthor:
    def __init__(self, user_id, name, display_name, bot=False):
        self.id = user_id
        self.name = name
        self.display_name = display_name
        self.bot = bot


class FakeChannel:
    def __init__(self, channel_id, name):
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id, name):
        self.id = guild_id
        self.name = name


def _message_with_image():
    return SimpleNamespace(
        id=111,
        content='check this out',
        clean_content='check this out',
        author=FakeAuthor(7, 'alice', 'Alice'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[
            SimpleNamespace(
                url='https://cdn/a.png',
                filename='cat.png',
                content_type='image/png',
            )
        ],
        mentions=[],
    )


def test_adapt_guild_message_and_persist():
    adapter = DiscordEventAdapter()
    message = SimpleNamespace(
        id=111,
        content='hello there',
        clean_content='hello there',
        author=FakeAuthor(7, 'alice', 'Alice'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[],
        mentions=[],
    )

    obs = adapter.adapt_message_event('alpha', 99, message)

    assert isinstance(obs, TextMessageObservation)
    assert obs.account_name == 'alpha'
    assert obs.guild_id == '33'
    assert obs.channel_name == 'general' and obs.message_id == '111'


def test_ignore_self_authored_message():
    adapter = DiscordEventAdapter()
    message = SimpleNamespace(
        id=111,
        content='self',
        clean_content='self',
        author=FakeAuthor(99, 'bot', 'Bot'),
        channel=FakeChannel(22, 'general'),
        guild=FakeGuild(33, 'Guild'),
        attachments=[],
        mentions=[],
    )

    obs = adapter.adapt_message_event('alpha', 99, message)

    assert obs is None


def test_adapt_dm_typing_event():
    adapter = DiscordEventAdapter()
    user = FakeAuthor(7, 'alice', 'Alice')
    channel = FakeChannel(44, 'Direct Message')

    obs = asyncio.run(adapter.adapt_typing_event('alpha', 99, channel, user, when=None))

    assert isinstance(obs, TypingObservation)
    assert obs.is_dm is True
    assert obs.guild_id == ''


class FakeImagesStore:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def resolve(self, **_kwargs):
        return SimpleNamespace(media=SimpleNamespace(images_in_enabled=self.enabled, max_images=4))


def test_adapt_then_fetch_images_off_loop_when_images_in_is_on():
    from plugins.discord.conversation.images import ImageLane
    calls = []
    lane = ImageLane(fetch=lambda url: (calls.append(url) or (b'PNG', 'image/png')))
    adapter = DiscordEventAdapter(image_lane=lane, settings_store=FakeImagesStore(True))
    obs = adapter.adapt_message_event('alpha', 99, _message_with_image(), fetch_images=False)
    assert isinstance(obs, TextMessageObservation) and obs.attachments and calls == []     # C2: nothing fetched inline
    assert adapter.fetch_images(obs) == 1 and calls == ['https://cdn/a.png']
    assert lane.cached('https://cdn/a.png') == (b'PNG', 'image/png')


def test_images_off_means_no_fetch():
    from plugins.discord.conversation.images import ImageLane
    calls = []
    lane = ImageLane(fetch=lambda url: (calls.append(url) or (b'PNG', 'image/png')))
    adapter = DiscordEventAdapter(image_lane=lane, settings_store=FakeImagesStore(False))
    obs = adapter.adapt_message_event('alpha', 99, _message_with_image())
    assert isinstance(obs, TextMessageObservation) and calls == [] and lane.cached('https://cdn/a.png') is None


def test_h6_ignored_channels_are_dropped_before_anything_stores_them():
    """Broadsword H6: the ignore check runs first — "fully ignored" means never stored."""
    class Store:
        def resolve(self):
            return SimpleNamespace(channel=SimpleNamespace(ignored_channels=['alpha:22']),
                                   media=SimpleNamespace(images_in_enabled=False, max_images=4))
    adapter = DiscordEventAdapter(settings_store=Store())
    assert adapter.adapt_message_event('alpha', 99, _message_with_image()) is None


def test_inbound_messages_never_touch_the_core_event_bus(monkeypatch):
    """S7 audit: the bus publish carried every message body into core's replay
    ring (no ephemeral flag, no subscriber). The hook is the one door."""
    import core.event_bus as bus
    calls = []
    monkeypatch.setattr(bus, 'publish', lambda *a, **k: calls.append(a))
    adapter = DiscordEventAdapter()
    assert adapter.adapt_message_event('alpha', 99, _message_with_image()) is not None
    assert calls == []
