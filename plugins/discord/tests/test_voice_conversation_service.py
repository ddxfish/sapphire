from plugins.discord.models.settings import SettingsOverlay, SettingsStore
from plugins.discord.models.voice import VoiceMode, VoiceSession
from plugins.discord.voice.voice_conversation_service import VoiceConversationService


class FakeExecution:
    def __init__(self):
        self.calls = []

    def execute(self, intention):
        self.calls.append(intention)
        return {'status': 'spoken'}


class FakeRepo:
    def list_transcripts(self, session_id, *, limit=100):
        return [{'speaker_name': 'Alice', 'text': 'Earlier line'}]


def _session():
    return VoiceSession(
        session_id='sess1',
        account_name='alpha',
        guild_id='g1',
        channel_id='vc1',
        mode=VoiceMode.CONVERSATIONAL,
    )


def _voice_store(*, enabled: bool = True, speaking_enabled: bool = True, core_enabled: bool = False) -> SettingsStore:
    store = SettingsStore()
    store.global_overlay = SettingsOverlay.from_dict({
        'voice': {
            'enabled': enabled,
            'speaking_enabled': speaking_enabled,
            'conversation_core_enabled': core_enabled,
        },
    })
    return store


def test_conversation_replies_in_conversational_mode(monkeypatch):
    execution = FakeExecution()
    service = VoiceConversationService(
        voice_execution_service=execution,
        voice_session_repository=FakeRepo(),
        settings_store=_voice_store(core_enabled=False),
        reply_style_service=FakeReplyStyle(),
    )
    monkeypatch.setattr(service, '_llm_reply', lambda prompt, settings=None: 'Hi there!')
    result = service.handle_transcript(_session(), {'status': 'transcribed', 'text': 'hey bot'})
    assert result['status'] == 'replied'
    assert execution.calls[0].text == 'Hi there!'


class FakeReplyStyle:
    def parse_llm_output(self, text, strip_thinking=True):
        from plugins.discord.conversation.reply_style_service import ParsedReply
        del strip_thinking
        return ParsedReply(chunks=[str(text).strip()])


def test_conversation_skips_when_core_path_enabled():
    service = VoiceConversationService(
        voice_execution_service=FakeExecution(),
        voice_session_repository=FakeRepo(),
        settings_store=_voice_store(core_enabled=True),
    )
    result = service.handle_transcript(_session(), {'status': 'transcribed', 'text': 'hey'})
    assert result['status'] == 'skipped'
    assert result['reason'] == 'core_conversation_active'


def test_conversation_skips_when_speaking_disabled():
    service = VoiceConversationService(
        voice_execution_service=FakeExecution(),
        voice_session_repository=FakeRepo(),
        settings_store=_voice_store(speaking_enabled=False),
    )
    result = service.handle_transcript(_session(), {'status': 'transcribed', 'text': 'hey'})
    assert result['status'] == 'skipped'
