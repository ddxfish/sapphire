"""Cognition debug ring buffer for Debug tab preview."""

from plugins.discord.observability.cognition_debug_service import CognitionDebugService


def test_cognition_debug_records_situation_intention_and_gate():
    svc = CognitionDebugService(situation_limit=3, intention_limit=5, gate_limit=5)
    svc.record_situation(
        account_name='alpha',
        channel_id='c1',
        channel_name='general',
        situation={
            'vibe': 'calm',
            'heat': 0.2,
            'silence_seconds': 120,
            'message_count': 4,
            'unique_authors': 2,
            'summary': 'Channel is calmly active.',
            'recent_topics': ['cats'],
            'built_at': 100.0,
        },
        organic_multiplier=1.0,
    )
    svc.record_intention(
        account_name='alpha',
        channel_id='c1',
        channel_name='general',
        kind='silent',
        score=0.4,
        reason='scored_silent',
        organic_multiplier=0.7,
        situation_vibe='calm',
        relationship={'familiarity': 0.1, 'fondness': 0.5},
    )
    svc.record_gate(
        gate='organic_miss',
        account_name='alpha',
        channel_id='c1',
        detail={'chance': 5.0, 'chance_multiplier': 0.7},
    )
    snap = svc.snapshot()
    assert len(snap['situations']) == 1
    assert snap['situations'][0]['vibe'] == 'calm'
    assert snap['situations'][0]['channel_name'] == 'general'
    assert snap['intentions'][0]['kind'] == 'silent'
    assert snap['gates'][0]['gate'] == 'organic_miss'


def test_cognition_debug_caps_situations_by_age():
    svc = CognitionDebugService(situation_limit=2)
    for i, built in enumerate([10.0, 20.0, 30.0]):
        svc.record_situation(
            account_name='alpha',
            channel_id=f'c{i}',
            situation={'vibe': 'quiet', 'built_at': built, 'summary': f'ch {i}'},
        )
    keys = {row['channel_id'] for row in svc.snapshot()['situations']}
    assert keys == {'c1', 'c2'}
