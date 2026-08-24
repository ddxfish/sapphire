from datetime import datetime

from plugins.discord.memory.birthday_service import BirthdayService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import EffectiveSettings, ProfileSettings, ProactiveSettings
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService


def _obs(content: str, **kwargs):
    base = dict(
        observation_id='obs-1',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u99',
        username='bob',
        display_name='Bob',
        message_id='m1',
        content=content,
        clean_content=content,
        created_at=0.0,
        is_dm=False,
        mentioned=False,
        attachments=[],
    )
    base.update(kwargs)
    return TextMessageObservation(**base)


def _stack(tmp_path):
    sqlite = SQLiteService(tmp_path / 'birthday.sqlite3')
    sqlite.start()
    profiles = ProfileRepository(sqlite)
    service = BirthdayService(profile_repository=profiles)
    settings = EffectiveSettings(
        profile=ProfileSettings(birthday_capture_enabled=True, birthday_followups_enabled=True),
        proactive=ProactiveSettings(greeting_utc_hour=9),
    )
    return profiles, service, settings


def test_capture_stores_birthday_on_profile(tmp_path, monkeypatch):
    profiles, service, settings = _stack(tmp_path)
    fixed = datetime(2026, 6, 30, 14, 30, 0)

    import plugins.discord.lib.server_time as server_time
    import plugins.discord.memory.birthday_service as birthday_module

    monkeypatch.setattr(server_time, 'now_local', lambda: fixed)
    monkeypatch.setattr(birthday_module, 'now_local', lambda: fixed)

    hints = service.try_capture_from_observation(_obs('my birthday is tomorrow'), settings)

    assert hints
    profile = profiles.get_or_create_profile('alpha', 'u99')
    assert profile['birthday_month'] == 7
    assert profile['birthday_day'] == 1
    assert profile['birthday_channel_id'] == 'c1'


def test_meta_chat_does_not_capture_birthday(tmp_path):
    profiles, service, settings = _stack(tmp_path)

    hints = service.try_capture_from_observation(
        _obs('where did Remmi learn today was your birthday?'),
        settings,
    )

    assert hints == []
    profile = profiles.get_or_create_profile('alpha', 'u99')
    assert int(profile.get('birthday_month') or 0) == 0


def test_bug_birthday_phrase_does_not_capture(tmp_path):
    profiles, service, settings = _stack(tmp_path)

    hints = service.try_capture_from_observation(_obs('is this a bug birthday'), settings)

    assert hints == []
    profile = profiles.get_or_create_profile('alpha', 'u99')
    assert int(profile.get('birthday_month') or 0) == 0


def test_evaluate_wishes_on_birthday_morning(tmp_path, monkeypatch):
    profiles, service, settings = _stack(tmp_path)
    profiles.set_birthday(
        'alpha',
        'u99',
        month=7,
        day=1,
        channel_id='c1',
        username='bob',
        display_name='Bob',
    )

    fixed = datetime(2026, 7, 1, 9, 0, 0)
    profiles.set_birthday_wish_run_at('alpha', 'u99', fixed.timestamp() - 1)

    intentions = service.evaluate_wishes('alpha', settings, now=fixed)

    assert len(intentions) == 1
    assert intentions[0].intention_type == 'birthday_wish'
    assert intentions[0].user_id == 'u99'
    assert intentions[0].metadata['mention'] == '<@u99>'


def test_evaluate_wishes_waits_until_scheduled_time(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    profiles.set_birthday('alpha', 'u99', month=7, day=1, channel_id='c1')
    future = datetime(2026, 7, 1, 15, 0, 0)
    profiles.set_birthday_wish_run_at('alpha', 'u99', future.timestamp())
    now = datetime(2026, 7, 1, 9, 0, 0)

    intentions = service.evaluate_wishes('alpha', settings, now=now)

    assert intentions == []


def test_spread_assigns_stable_times_for_multiple_users(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    for user_id in ('u1', 'u2', 'u3'):
        profiles.set_birthday('alpha', user_id, month=7, day=1, channel_id='c1')

    now = datetime(2026, 7, 1, 8, 0, 0)
    service.evaluate_wishes('alpha', settings, now=now)

    run_ats = [
        float(profiles.get_or_create_profile('alpha', user_id).get('birthday_wish_run_at') or 0)
        for user_id in ('u1', 'u2', 'u3')
    ]
    assert all(ts > 0 for ts in run_ats)
    assert len(set(run_ats)) == 3


def test_evaluate_wishes_dedupes_per_year(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    profiles.set_birthday('alpha', 'u99', month=7, day=1, channel_id='c1')
    profiles.mark_birthday_wished('alpha', 'u99', 2026)

    intentions = service.evaluate_wishes('alpha', settings, now=datetime(2026, 7, 1, 9, 0, 0))

    assert intentions == []


def test_bulk_wish_when_channel_exceeds_threshold(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    settings.profile.birthday_bulk_enabled = True
    settings.profile.birthday_bulk_threshold = 3
    now = datetime(2026, 7, 1, 12, 0, 0)
    for user_id in ('u1', 'u2', 'u3', 'u4'):
        profiles.set_birthday('alpha', user_id, month=7, day=1, channel_id='c1')
        profiles.set_birthday_wish_run_at('alpha', user_id, now.timestamp() - 1)

    intentions = service.evaluate_wishes('alpha', settings, now=now)

    assert len(intentions) == 1
    assert intentions[0].reason == 'profile_birthday_bulk'
    assert intentions[0].metadata['bulk'] is True
    assert len(intentions[0].metadata['recipients']) == 4


def test_bulk_disabled_keeps_individual_wishes(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    settings.profile.birthday_bulk_enabled = False
    now = datetime(2026, 7, 1, 12, 0, 0)
    for user_id in ('u1', 'u2', 'u3', 'u4'):
        profiles.set_birthday('alpha', user_id, month=7, day=1, channel_id='c1')
        profiles.set_birthday_wish_run_at('alpha', user_id, now.timestamp() - 1)

    intentions = service.evaluate_wishes('alpha', settings, now=now)

    assert len(intentions) == 4
    assert all(intent.reason == 'profile_birthday' for intent in intentions)


def test_mark_wished_marks_all_bulk_recipients(tmp_path):
    profiles, service, settings = _stack(tmp_path)
    from plugins.discord.models.intentions import BirthdayWishIntention

    for user_id in ('u1', 'u2', 'u3', 'u4'):
        profiles.set_birthday('alpha', user_id, month=7, day=1, channel_id='c1')

    service.mark_wished(BirthdayWishIntention(
        intention_type='birthday_wish',
        account_name='alpha',
        channel_id='c1',
        message_id='',
        reason='profile_birthday_bulk',
        user_id='u1',
        metadata={
            'bulk': True,
            'recipients': [
                {'user_id': 'u1', 'display_name': 'One', 'mention': '<@u1>'},
                {'user_id': 'u2', 'display_name': 'Two', 'mention': '<@u2>'},
                {'user_id': 'u3', 'display_name': 'Three', 'mention': '<@u3>'},
                {'user_id': 'u4', 'display_name': 'Four', 'mention': '<@u4>'},
            ],
        },
    ))

    for user_id in ('u1', 'u2', 'u3', 'u4'):
        profile = profiles.get_or_create_profile('alpha', user_id)
        assert profile['last_birthday_wish_year'] == 2026
