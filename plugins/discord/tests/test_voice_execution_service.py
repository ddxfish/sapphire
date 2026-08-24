from plugins.discord.models.intentions import SpeakVoiceIntention
from plugins.discord.voice.voice_execution_service import VoiceExecutionService


class FakeSpeechBridge:
    def synthesize_speech(self, text):
        return {'audio_bytes': b'spoken', 'format': 'pcm'}


class FakeTransport:
    def __init__(self):
        self.played = []

    def play_audio_sync(self, account_name, channel_id, audio_bytes, **kwargs):
        self.played.append((account_name, channel_id, audio_bytes))
        return {'status': 'played'}


def test_speak_intention_via_tts():
    transport = FakeTransport()
    service = VoiceExecutionService(speech_bridge=FakeSpeechBridge(), voice_transport=transport)

    intention = SpeakVoiceIntention(
        intention_type='speak_voice',
        account_name='alpha',
        channel_id='vc1',
        message_id='',
        reason='approved_reply',
        text='Hi there',
    )
    result = service.execute(intention)

    assert result['status'] == 'spoken'
    assert transport.played[0][2] == b'spoken'


def test_speak_blocked_when_policy_denies():
    from plugins.discord.models.settings import SettingsStore

    service = VoiceExecutionService(
        speech_bridge=FakeSpeechBridge(),
        voice_transport=FakeTransport(),
        policy_service=type('P', (), {'evaluate_voice_speak': staticmethod(lambda *a, **k: {'allowed': False, 'reason': 'disabled'})})(),
        settings_store=SettingsStore(),
    )
    intention = SpeakVoiceIntention(
        intention_type='speak_voice',
        account_name='alpha',
        channel_id='vc1',
        message_id='',
        reason='approved_reply',
        text='Hi',
    )
    result = service.execute(intention)
    assert result['status'] == 'blocked'


def test_voice_conversation_not_blocked_by_recent_human_activity():
    from plugins.discord.voice.voice_turn_taking_service import VoiceTurnTakingService

    turn_taking = VoiceTurnTakingService(min_silence_seconds=1.0)
    turn_taking.note_speech_activity('vc1')
    transport = FakeTransport()
    service = VoiceExecutionService(
        speech_bridge=FakeSpeechBridge(),
        voice_transport=transport,
        turn_taking_service=turn_taking,
    )
    intention = SpeakVoiceIntention(
        intention_type='speak_voice',
        account_name='alpha',
        channel_id='vc1',
        message_id='',
        reason='voice_conversation',
        text='Hi there',
    )
    result = service.execute(intention)
    assert result['status'] == 'spoken'
