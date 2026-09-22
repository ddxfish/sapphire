from plugins.discord.observability.llm_debug_service import LlmDebugService


def test_llm_debug_keeps_last_ten_and_records_typo_delivery():
    service = LlmDebugService(limit=10)
    for index in range(12):
        message_id = f'msg-{index}'
        service.record_prompt({
            'message_id': message_id,
            'account': 'bot',
            'content': f'hello {index}',
        })
        service.record_response(
            message_id,
            raw_text=f'reply {index}',
            parsed_chunks=[f'reply {index}'],
            status='sent',
            task={'name': 'Discord Reply', 'provider': 'claude', 'model': 'claude-sonnet-5'},
            event_data={'account': 'bot'},
            delivery={
                'typo_applied': index == 11,
                'sent_text': 'teh' if index == 11 else f'reply {index}',
                'corrected_text': 'the' if index == 11 else f'reply {index}',
                'edit_delay_seconds': 2.5 if index == 11 else 0.0,
                'edit_kind': 'auto_typo' if index == 11 else '',
                'quote_reply_to': '',
                'chunks_sent': 1,
            },
        )

    entries = service.list_entries()
    assert len(entries) == 10
    assert entries[0]['message_id'] == 'msg-11'
    assert entries[0]['delivery']['typo_applied'] is True
    assert entries[0]['llm'] == {'configured_primary': 'claude', 'configured_model': 'claude-sonnet-5', 'task_name': 'Discord Reply', 'task_id': ''}
    assert entries[-1]['message_id'] == 'msg-2'


def test_llm_debug_records_policy_rejection():
    service = LlmDebugService(limit=10)
    service.record_rejection(
        message_id='m1',
        account='bot',
        channel_id='c1',
        username='alice',
        content='hey bot',
        reason='cooldown',
        stage='policy',
        detail={'seconds_remaining': 12},
    )
    entries = service.list_entries()
    assert len(entries) == 1
    assert entries[0]['kind'] == 'rejection'
    assert entries[0]['rejection']['reason'] == 'cooldown'
