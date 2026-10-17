from plugins.discord.transport.voice_occupancy import voice_channel_occupancy


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeVoiceState:
    def __init__(self, channel, user):
        self.channel = channel
        self.user = user


class FakeVoiceChannel:
    def __init__(self, channel_id, guild=None, members=None):
        self.id = channel_id
        self.guild = guild
        self.members = members or []


class FakeGuild:
    def __init__(self, voice_states=None, members=None, voice_client=None):
        self.voice_states = voice_states or []
        self.members = members or []
        self.voice_client = voice_client


def test_counts_humans_from_voice_states_when_members_cache_empty():
    guild = FakeGuild()
    channel = FakeVoiceChannel(300, guild=guild, members=[])
    guild.voice_states = [
        FakeVoiceState(channel, FakeUser(1001)),
        FakeVoiceState(channel, FakeUser(1002)),
    ]

    occupancy = voice_channel_occupancy(channel, bot_id=42)

    assert occupancy['human_count'] == 2
    assert occupancy['member_count'] == 2
    assert occupancy['bot_connected'] is False


def test_excludes_bot_from_human_count():
    guild = FakeGuild()
    channel = FakeVoiceChannel(300, guild=guild)
    guild.voice_states = [
        FakeVoiceState(channel, FakeUser(42)),
        FakeVoiceState(channel, FakeUser(1001)),
    ]

    occupancy = voice_channel_occupancy(channel, bot_id='42')

    assert occupancy['human_count'] == 1
    assert occupancy['bot_connected'] is True


def test_falls_back_to_channel_members():
    guild = FakeGuild(voice_states=[])
    channel = FakeVoiceChannel(300, guild=guild, members=[FakeUser(1001)])

    occupancy = voice_channel_occupancy(channel, bot_id=42)

    assert occupancy['human_count'] == 1
    assert occupancy['member_count'] == 1
