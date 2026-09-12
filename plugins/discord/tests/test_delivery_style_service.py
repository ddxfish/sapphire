import random

from plugins.discord.conversation.delivery_style_service import DeliveryStyleService
from plugins.discord.conversation.edit_history_service import EditHistoryService
from plugins.discord.conversation.reply_style_service import ParsedReply, ReplyStyleService
from plugins.discord.models.settings import DeliverySettings, EffectiveSettings


def test_reply_style_parses_edit_tag():
    service = ReplyStyleService()
    parsed = service.parse_llm_output('Hello there [edit:Hello there!]')
    assert parsed.chunks == ['Hello there']
    assert parsed.edit_text == 'Hello there!'


def test_plan_explicit_edit(monkeypatch):
    monkeypatch.setattr(random, 'uniform', lambda *_: 2.5)
    delivery = DeliveryStyleService()
    settings = EffectiveSettings(delivery=DeliverySettings(message_edits_enabled=True))
    parsed = ParsedReply(chunks=['Hello there'], edit_text='Hello there!')
    plan = delivery.plan_delivery(
        parsed=parsed,
        raw_text='Hello there [edit:Hello there!]',
        event_data={'message_id': 'm1', 'content': 'hi'},
        settings=settings,
        trigger_content='hi',
    )
    assert plan.chunks[0] == 'Hello there'
    assert plan.edit_text == 'Hello there!'
    assert plan.edit_delay == 2.5


def test_plan_auto_typo(monkeypatch):
    monkeypatch.setattr(random, 'random', lambda: 0.0)
    monkeypatch.setattr(random, 'uniform', lambda *_: 3.0)
    delivery = DeliveryStyleService()
    settings = EffectiveSettings(
        delivery=DeliverySettings(
            message_edits_enabled=True,
            auto_typo_enabled=True,
            auto_typo_chance=100,
        ),
    )
    parsed = ParsedReply(chunks=['I definitely agree with that'])
    plan = delivery.plan_delivery(
        parsed=parsed,
        raw_text='I definitely agree with that',
        event_data={'message_id': 'm1', 'content': 'thoughts'},
        settings=settings,
        trigger_content='thoughts',
    )
    assert plan.chunks[0] != 'I definitely agree with that'
    assert plan.edit_text == 'I definitely agree with that'
    assert plan.edit_delay == 3.0


def test_quote_reply_skips_joke_comment(monkeypatch):
    monkeypatch.setattr(random, 'random', lambda: 0.0)
    delivery = DeliveryStyleService()
    settings = EffectiveSettings(delivery=DeliverySettings(quote_reply_enabled=True))
    parsed = ParsedReply(chunks=['lmao fair'])
    plan = delivery.plan_delivery(
        parsed=parsed,
        raw_text='lmao fair',
        event_data={'message_id': 'm1', 'content': 'that was wild', 'batch_size': 1},
        settings=settings,
        trigger_content='that was wild',
    )
    assert plan.reply_to_message_id is None


def test_edit_history_prompt_hint():
    history = EditHistoryService()
    history.record('alpha', 'c1', message_id='bot1', before='teh plan', after='the plan')
    hint = history.build_prompt_hint('alpha', 'c1')
    assert 'self-edits' in hint
    assert 'teh plan' in hint
