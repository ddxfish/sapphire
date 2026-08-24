from unittest.mock import MagicMock

from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.sapphire.voice_event_bridge import VoiceEventBridge
from plugins.discord.sapphire.voice_chat import voice_chat_name


class FakeRepo:
    def __init__(self, session):
        self.session = session

    def get_active_by_guild_channel(self, guild_id, channel_id):
        if self.session.guild_id == guild_id and self.session.channel_id == channel_id:
            return self.session
        return None


def test_voice_turn_start_records_trace_and_world_model():
    session = VoiceSession(
        session_id='sess-1',
        account_name='bot',
        guild_id='111',
        channel_id='222',
        mode=VoiceMode.CONVERSATIONAL,
    )
    traces = []
    world = MagicMock()
    bridge = VoiceEventBridge(
        voice_session_repository=FakeRepo(session),
        trace_repository=MagicMock(record_trace=lambda t, m, p: traces.append((t, p))),
        world_model_service=world,
    )
    chat = voice_chat_name('111', '222')
    bridge._handle_voice_turn('voice_turn_start', chat, {
        'message_id': 'm1',
        'user_text': 'Leona what time is it',
    })
    assert traces
    assert traces[0][0] == 'voice_conversation_turn'
    world._record_observation.assert_called_once()


def test_non_discord_chat_ignored():
    traces = []
    bridge = VoiceEventBridge(
        trace_repository=MagicMock(record_trace=lambda t, m, p: traces.append(t)),
    )
    bridge._handle({
        'type': 'voice_turn_start',
        'data': {'chat': 'phone:call', 'user_text': 'hi'},
    }, MagicMock(VOICE_TURN_START='voice_turn_start', TTS_PLAYING='tts_playing', TTS_STOPPED='tts_stopped'))
    assert traces == []
