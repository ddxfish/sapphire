from plugins.discord.cognition.attention_service import AttentionService


def test_mention_boosts_user_activation():
    service = AttentionService()

    service.apply_signal('user', 'u1', 'alpha', boost=0.2, reason='message')
    service.apply_signal('user', 'u1', 'alpha', boost=0.5, reason='mentioned')

    scores = service.top_entities('alpha', limit=5)
    assert scores[0]['entity_id'] == 'u1'
    assert scores[0]['score'] >= 0.5


def test_activation_decays_over_time():
    service = AttentionService(decay_rate=0.5)
    service.apply_signal('channel', 'c1', 'alpha', boost=1.0, reason='active', now=0.0)

    service.decay('alpha', now=10.0)
    scores = service.top_entities('alpha', limit=5)

    assert scores[0]['score'] < 1.0
