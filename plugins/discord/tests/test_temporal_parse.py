from datetime import datetime

from plugins.discord.cognition.temporal_parse import (
    extract_birthday_date,
    extract_birthday_run_at,
    extract_commitment_run_at,
    extract_reminder_run_at,
    passes_birthday_capture_gate,
    passes_commitment_gate,
    passes_reminder_gate,
)


def _now():
    return datetime(2026, 6, 30, 14, 30, 0)


def test_gate_ignores_casual_chat():
    assert passes_commitment_gate('lol that was funny') is False
    assert passes_commitment_gate('see you later') is False


def test_gate_accepts_commitment_phrases_only():
    assert passes_commitment_gate('my birthday is tomorrow') is False
    assert passes_commitment_gate("next week I'll push to dev") is True
    assert passes_commitment_gate('in 3 days I will deploy the fix') is True


def test_birthday_capture_gate_rejects_meta_chat():
    assert passes_birthday_capture_gate('where did Remmi learn today was your birthday?') is False
    assert passes_birthday_capture_gate('is this a bug birthday') is False
    assert passes_birthday_capture_gate('my birthday is tomorrow') is True


def test_birthday_date_tomorrow():
    result = extract_birthday_date('my birthday is tomorrow', _now())
    assert result is not None
    month, day, label = result
    assert month == 7
    assert day == 1
    assert 'tomorrow' in label.lower() or label == 'tomorrow'


def test_birthday_tomorrow():
    result = extract_birthday_run_at('my birthday is tomorrow', _now())
    assert result is not None
    run_at, label = result
    assert run_at.day == 1
    assert run_at.month == 7
    assert run_at.hour == 9
    assert 'tomorrow' in label.lower() or label == 'tomorrow'


def test_birthday_friday():
    result = extract_birthday_run_at('my birthday is on Friday', _now())
    assert result is not None
    run_at, _label = result
    assert run_at.weekday() == 4  # Friday
    assert run_at.hour == 9


def test_commitment_in_three_days():
    result = extract_commitment_run_at('in 3 days I will ship the hotfix', _now())
    assert result is not None


def test_reminder_gate_and_extract_five_minutes():
    text = 'can you remind me in 5 minutes to make a coffee'
    assert passes_reminder_gate(text) is True
    assert passes_commitment_gate(text) is False
    result = extract_reminder_run_at(text, _now())
    assert result is not None
    run_at, body, label = result
    assert body == 'make a coffee'
    assert 'minute' in label.lower()
    assert (run_at - _now()).total_seconds() == 300


def test_reminder_glued_minutes_without_space():
    text = 'thank you, can you remind me in 5minutes to drink water'
    assert passes_reminder_gate(text) is True
    result = extract_reminder_run_at(text, _now())
    assert result is not None
    _run_at, body, _label = result
    assert 'drink water' in body


def test_reminder_to_call_in_one_hour():
    result = extract_reminder_run_at('remind me to call mom in 1 hour', _now())
    assert result is not None
    _run_at, body, _label = result
    assert 'call mom' in body.lower()


def test_reminder_without_in_before_minutes():
    text = '@Fox!Remmi can you remind me 2 minutes to eat a mud pie?'
    assert passes_reminder_gate(text) is True
    result = extract_reminder_run_at(text, _now())
    assert result is not None
    run_at, body, label = result
    assert body == 'eat a mud pie'
    assert 'minute' in label.lower()
    assert (run_at - _now()).total_seconds() == 120


def test_reminder_without_dateparser(monkeypatch):
    import plugins.discord.cognition.temporal_parse as temporal_parse
    monkeypatch.setattr(temporal_parse, 'search_dates', None)
    text = 'remind me in 5 minutes to drink water'
    result = extract_reminder_run_at(text, _now())
    assert result is not None
    _run_at, body, _label = result
    assert 'drink water' in body


def test_commitment_next_week():
    result = extract_commitment_run_at("next week I'll be pushing to the dev build", _now())
    assert result is not None
    run_at, body = result
    assert (run_at - _now()).days >= 6
    assert 'dev build' in body.lower()


def test_birthday_does_not_create_commitment():
    text = 'my birthday is tomorrow'
    assert extract_birthday_run_at(text, _now()) is not None
    assert extract_commitment_run_at(text, _now()) is None
