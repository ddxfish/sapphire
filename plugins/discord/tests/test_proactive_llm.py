from plugins.discord.models.intentions import BirthdayWishIntention, GreetChannelIntention, OutreachIntention
from plugins.discord.models.settings import CognitiveSettings, EffectiveSettings, ProactiveSettings
from plugins.discord.proactive.proactive_executor import ProactiveExecutor
from plugins.discord.proactive.proactive_message_service import DEFAULT_INSTRUCTIONS, ProactiveMessageService


def _settings(**proactive_kwargs):
    return EffectiveSettings(proactive=ProactiveSettings(**proactive_kwargs))


def _greet(channel_id='c1'):
    return GreetChannelIntention(
        intention_type='greet_channel',
        account_name='alpha',
        channel_id=channel_id,
        message_id='',
        reason='morning_greeting',
        prompt='',
    )


def test_build_greeting_uses_instructions_when_llm_disabled():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=False, greeting_message='Custom template', greeting_fallback='Fallback')
    assert service.build_greeting('alpha', 'c1', settings) == 'Custom template'


def test_build_greeting_uses_fallback_when_llm_disabled_without_instructions():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=False, greeting_message='', greeting_fallback='Static morning!')
    assert service.build_greeting('alpha', 'c1', settings) == 'Static morning!'


def test_build_greeting_uses_fallback_when_llm_enabled():
    """With use_llm on, static text is only the post-rejection fallback — never the instructions."""
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=True, greeting_message='Write a warm greeting', greeting_fallback='Morning all!')
    assert service.build_greeting('alpha', 'c1', settings) == 'Morning all!'


def test_build_goodnight_uses_fallback_when_llm_enabled():
    service = ProactiveMessageService()
    settings = _settings(goodnight_use_llm=True, goodnight_fallback='Night night!')
    assert service.build_goodnight('alpha', 'c1', settings) == 'Night night!'


def test_event_payload_greeting_defaults():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=True)
    payload = service.build_event_payload(_greet(), settings, kind='greeting', account_name='alpha', guild_id='g1')
    assert payload['proactive_kind'] == 'greeting'
    assert payload['account'] == 'alpha'
    assert payload['channel_id'] == 'c1'
    assert payload['guild_id'] == 'g1'
    assert payload['content'] == DEFAULT_INSTRUCTIONS['greeting']
    assert payload['message_id'].startswith('proactive-greeting-c1-')
    assert 'llm_primary' not in payload


def test_event_payload_uses_custom_instructions():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=True, greeting_message='Say hi in pirate speak')
    payload = service.build_event_payload(_greet(), settings, kind='greeting', account_name='alpha')
    assert payload['content'] == 'Say hi in pirate speak'


def test_event_payload_none_when_use_llm_off():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=False)
    assert service.build_event_payload(_greet(), settings, kind='greeting', account_name='alpha') is None


def test_event_payload_carries_per_kind_llm_override():
    service = ProactiveMessageService()
    settings = _settings(greeting_use_llm=True, greeting_model_provider='ollama', greeting_model_name='llama3.2')
    payload = service.build_event_payload(_greet(), settings, kind='greeting', account_name='alpha')
    assert payload['llm_primary'] == 'ollama'
    assert payload['llm_model'] == 'llama3.2'


def test_event_payload_outreach_inherits_reply_llm():
    service = ProactiveMessageService()
    settings = EffectiveSettings(
        proactive=ProactiveSettings(),
        cognitive=CognitiveSettings(llm_primary='claude', llm_model='sonnet'),
    )
    intention = OutreachIntention(
        intention_type='outreach', account_name='alpha', channel_id='c2',
        message_id='', reason='quiet_outreach', prompt='Checking in',
    )
    payload = service.build_event_payload(intention, settings, kind='outreach', account_name='alpha')
    assert payload['content'] == DEFAULT_INSTRUCTIONS['outreach']
    assert payload['llm_primary'] == 'claude'
    assert payload['llm_model'] == 'sonnet'


def test_event_payload_birthday_instructions():
    service = ProactiveMessageService()
    settings = _settings(birthday_use_llm=True)
    single = BirthdayWishIntention(
        intention_type='birthday_wish', account_name='alpha', channel_id='c1',
        message_id='', reason='birthday', prompt='',
        metadata={'display_name': 'Zeebie', 'mention': '<@42>'},
    )
    payload = service.build_event_payload(single, settings, kind='birthday', account_name='alpha')
    assert "Zeebie's birthday" in payload['content']
    assert '<@42>' in payload['content']

    bulk = BirthdayWishIntention(
        intention_type='birthday_wish', account_name='alpha', channel_id='c1',
        message_id='', reason='birthday', prompt='',
        metadata={'bulk': True, 'recipients': [
            {'display_name': 'A', 'mention': '<@1>'},
            {'display_name': 'B', 'mention': '<@2>'},
        ]},
    )
    payload = service.build_event_payload(bulk, settings, kind='birthday', account_name='alpha')
    assert 'A (<@1>)' in payload['content']
    assert 'B (<@2>)' in payload['content']


class _FakeBridge:
    def __init__(self, accepted):
        self.accepted = accepted
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return self.accepted


class _FakeTransport:
    def __init__(self):
        self.messages = []

    def send_message_sync(self, channel_id, text, reply_to_message_id=None, account_name=None, guild_id=None):
        self.messages.append({'channel_id': channel_id, 'text': text})
        return {'status': 'sent', 'channel_id': str(channel_id)}


class _FakeStore:
    def __init__(self, settings):
        self.settings = settings

    def resolve(self, **kwargs):
        return self.settings


class _FakeGreetingService:
    def __init__(self):
        self.marked = []

    def mark_sent(self, intention):
        self.marked.append(intention)


def test_executor_queues_greeting_via_pipeline():
    settings = _settings(greeting_use_llm=True, greeting_fallback='Fallback')
    bridge = _FakeBridge(accepted=True)
    transport = _FakeTransport()
    greeting_service = _FakeGreetingService()
    executor = ProactiveExecutor(
        transport=transport,
        greeting_service=greeting_service,
        event_bridge=bridge,
        settings_store=_FakeStore(settings),
        proactive_message_service=ProactiveMessageService(),
    )
    result = executor.execute(_greet())
    assert result == {'status': 'sent', 'delivery': 'pipeline'}
    assert bridge.payloads[0]['proactive_kind'] == 'greeting'
    assert greeting_service.marked, 'cooldown must be marked at accept time'
    assert transport.messages == []


def test_executor_falls_back_to_static_when_event_rejected():
    settings = _settings(greeting_use_llm=True, greeting_fallback='Fallback morning')
    bridge = _FakeBridge(accepted=False)
    transport = _FakeTransport()
    executor = ProactiveExecutor(
        transport=transport,
        event_bridge=bridge,
        settings_store=_FakeStore(settings),
        proactive_message_service=ProactiveMessageService(),
    )
    result = executor.execute(_greet())
    assert result['status'] == 'sent'
    assert result.get('delivery') != 'pipeline'
    assert transport.messages[0]['text'] == 'Fallback morning'


def test_executor_static_path_when_use_llm_off():
    settings = _settings(greeting_use_llm=False, greeting_message='Static hello')
    bridge = _FakeBridge(accepted=True)
    transport = _FakeTransport()
    executor = ProactiveExecutor(
        transport=transport,
        event_bridge=bridge,
        settings_store=_FakeStore(settings),
        proactive_message_service=ProactiveMessageService(),
    )
    result = executor.execute(_greet())
    assert result['status'] == 'sent'
    assert bridge.payloads == []
    assert transport.messages[0]['text'] == 'Static hello'
