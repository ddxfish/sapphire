from plugins.discord.models.intentions import GreetChannelIntention
from plugins.discord.models.settings import EffectiveSettings, ProactiveSettings
from plugins.discord.proactive.proactive_executor import ProactiveExecutor


class FakeTransport:
    def __init__(self):
        self.messages = []

    def send_message_sync(self, channel_id, text, reply_to_message_id=None, account_name=None, guild_id=None):
        self.messages.append({'channel_id': channel_id, 'text': text, 'account_name': account_name, 'guild_id': guild_id})
        return {'status': 'sent', 'channel_id': str(channel_id)}


class FakeMessageService:
    def build_greeting(self, account_name, channel_id, settings):
        return 'LLM greeting text'

    def build_goodnight(self, account_name, channel_id, settings):
        return 'LLM goodnight text'


class FakeSettingsStore:
    def __init__(self, settings):
        self.settings = settings

    def resolve(self, **kwargs):
        return self.settings


def test_executor_resolves_greeting_via_message_service():
    transport = FakeTransport()
    settings = EffectiveSettings(proactive=ProactiveSettings(greeting_fallback='Fallback'))
    executor = ProactiveExecutor(
        transport=transport,
        settings_store=FakeSettingsStore(settings),
        proactive_message_service=FakeMessageService(),
    )
    intention = GreetChannelIntention(
        intention_type='greet_channel',
        account_name='alpha',
        channel_id='c1',
        message_id='',
        reason='morning_greeting',
        prompt='',
    )
    result = executor.execute(intention)
    assert result.get('status') == 'sent'
    assert transport.messages[0]['text'] == 'LLM greeting text'
