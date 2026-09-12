from plugins.discord.cognition.cognitive_orchestrator import CognitiveOrchestrator
from plugins.discord.conversation.conversation_service import ConversationService
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.models.intentions import ReplyMessageIntention
from plugins.discord.proactive.proactive_executor import ProactiveExecutor
from plugins.discord.storage.repositories.tasks import TaskRepository
from plugins.discord.storage.sqlite import SQLiteService


class FakeTransport:
    def __init__(self):
        self.sent = []

    def send_message_sync(self, channel_id, text, *, reply_to_message_id=None, account_name=None, guild_id=None):
        self.sent.append({
            'channel_id': channel_id,
            'text': text,
            'reply_to_message_id': reply_to_message_id,
            'account_name': account_name,
        })
        return {'status': 'ok', 'messages': [{'message_id': 'bot-1'}]}

    def hold_typing_sync(self, channel_id, duration, *, account_name=None):
        return None


class FakeBridge:
    def __init__(self, accepted=False):
        self.accepted = accepted
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return self.accepted


class FakeTraceRepo:
    def record_trace(self, trace_type, summary, detail=None):
        pass


class FakeWorldModel:
    def __init__(self):
        sqlite = SQLiteService(':memory:')
        sqlite.start()
        self.task_repository = TaskRepository(sqlite)
        self.task_repository.create_task('alpha', 'reminder_follow_up', target_id='c1', run_at=0)


def test_task_follow_up_direct_fallback_when_event_rejected():
    transport = FakeTransport()
    executor = ProactiveExecutor(transport=transport, event_bridge=FakeBridge(accepted=False))
    intention = ReplyMessageIntention(
        intention_type='reply_message',
        account_name='alpha',
        channel_id='c1',
        message_id='',
        reason='task:reminder_follow_up',
        prompt='Deliver reminder',
        metadata={
            'use_llm': True,
            'task_id': 7,
            'task_type': 'reminder_follow_up',
            'event_payload': {
                'author_id': 'u1',
                'reminder': 'drink water',
                'when_label': 'in 5 minutes',
            },
        },
    )
    result = executor.execute(intention)
    assert result['status'] == 'sent'
    assert transport.sent
    assert 'drink water' in transport.sent[0]['text']
    assert '<@u1>' in transport.sent[0]['text']


def test_handle_llm_response_skips_synthetic_reply_target_and_completes_task():
    transport = FakeTransport()
    world = FakeWorldModel()
    orchestrator = CognitiveOrchestrator(
        intent_engine=None,
        world_state_builder=None,
        world_model_service=world,
    )
    service = ConversationService(
        event_bridge=None,
        policy_service=None,
        prompt_context_service=None,
        trace_repository=FakeTraceRepo(),
        reply_style_service=ReplyStyleService(),
        transport=transport,
        cognitive_orchestrator=orchestrator,
    )
    event_data = {
        'message_id': 'task-followup-1',
        'channel_id': 'c1',
        'account': 'alpha',
        'task_follow_up': 'true',
        'task_id': '1',
        'content': 'Remind them to drink water',
    }
    result = service.handle_llm_response({}, event_data, 'Hey <@u1>, drink some water!')
    assert result['status'] == 'sent'
    assert transport.sent[0]['reply_to_message_id'] is None
    tasks = world.task_repository.list_tasks('alpha', status='completed')
    assert len(tasks) == 1


def test_handle_llm_response_completes_task_when_tool_already_sent():
    transport = FakeTransport()
    world = FakeWorldModel()
    orchestrator = CognitiveOrchestrator(
        intent_engine=None,
        world_state_builder=None,
        world_model_service=world,
    )
    style = ReplyStyleService()
    style.mark_tool_sent('task-followup-1', 'already sent via tool')
    service = ConversationService(
        event_bridge=None,
        policy_service=None,
        prompt_context_service=None,
        trace_repository=FakeTraceRepo(),
        reply_style_service=style,
        transport=transport,
        cognitive_orchestrator=orchestrator,
    )
    event_data = {
        'message_id': 'task-followup-1',
        'channel_id': 'c1',
        'account': 'alpha',
        'task_follow_up': 'true',
        'task_id': '1',
    }
    result = service.handle_llm_response({}, event_data, 'duplicate prose')
    assert result['status'] == 'skipped'
    assert transport.sent == []
    tasks = world.task_repository.list_tasks('alpha', status='completed')
    assert len(tasks) == 1
