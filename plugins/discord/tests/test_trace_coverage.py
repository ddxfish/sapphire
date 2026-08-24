from plugins.discord.observability.trace_service import TraceService
from plugins.discord.storage.repositories.traces import TraceRepository
from plugins.discord.storage.sqlite import SQLiteService


def _service(tmp_path):
    sqlite = SQLiteService(tmp_path / 'traces.sqlite3')
    sqlite.start()
    return TraceService(trace_repository=TraceRepository(sqlite))


def test_records_structured_trace_categories(tmp_path):
    service = _service(tmp_path)
    service.record_intention('reply_message', {'channel_id': 'c1', 'reason': 'mentioned'})
    service.record_policy_rejection('cooldown', {'channel_id': 'c1'})
    service.record_memory_injection({'pinned': 1, 'recalled': 2})
    service.record_voice_decision('speak_blocked', {'reason': 'turn_taking'})

    traces = service.list_recent(limit=10)
    types = {item['trace_type'] for item in traces}
    assert 'intention_generated' in types
    assert 'policy_rejected' in types
    assert 'memory_injected' in types
    assert 'voice_decision' in types


def test_trace_detail_is_structured_without_prompt_dump(tmp_path):
    service = _service(tmp_path)
    service.record_affect_modulation({'energy_delta': -0.01, 'sociability_delta': 0.02})

    trace = service.list_recent(limit=1)[0]
    assert 'energy_delta' in trace['detail']
    assert 'prompt' not in trace['detail']
