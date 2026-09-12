"""Memory system: repo lookups, tool actions, and the profile prompt hint."""

from plugins.discord.conversation.batching_service import BatchingService
from plugins.discord.conversation.conversation_service import ConversationService, _profile_prompt_hint
from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.models.observations import TextMessageObservation
from plugins.discord.models.settings import SettingsStore
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService


def _repo(tmp_path):
    sqlite = SQLiteService(tmp_path / 'memory.sqlite3')
    sqlite.start()
    return ProfileRepository(sqlite)


def test_record_interaction_stamps_names(tmp_path):
    repo = _repo(tmp_path)
    service = ProfileService(profile_repository=repo)
    service.record_interaction('alpha', 'u1', username='krem', display_name='Krem')
    row = repo.find_user('alpha', 'Krem')
    assert row and row['user_id'] == 'u1'
    assert repo.find_user('alpha', 'krem')['user_id'] == 'u1'
    assert repo.find_user('alpha', 'u1')['user_id'] == 'u1'
    assert repo.find_user('alpha', 'nobody') is None


def test_fact_search_and_scoped_delete(tmp_path):
    repo = _repo(tmp_path)
    service = ProfileService(profile_repository=repo)
    service.record_interaction('alpha', 'u1', username='krem')
    service.remember_fact('alpha', 'u1', 'likes synthwave')
    service.remember_fact('alpha', 'u1', 'hates olives')
    hits = repo.search_facts('alpha', 'synthwave')
    assert len(hits) == 1 and hits[0]['username'] == 'krem'
    # empty match must never wipe
    assert repo.delete_facts('alpha', 'u1', match='') == 0
    assert repo.delete_facts('alpha', 'u1', match='olives') == 1
    assert len(repo.list_facts('alpha', 'u1')) == 1


def test_profile_prompt_hint_formats_facts():
    hint = _profile_prompt_hint(
        {'summary': 'An old friend.', 'facts': [{'content': 'likes synthwave'}, {'content': ''}]},
        'Krem',
    )
    assert 'Stored notes about Krem' in hint
    assert 'An old friend.' in hint
    assert '- likes synthwave' in hint
    assert _profile_prompt_hint({}, 'Krem') == ''
    assert _profile_prompt_hint({'summary': '', 'facts': []}, 'Krem') == ''


class _Bridge:
    def __init__(self):
        self.payloads = []

    def emit_discord_message(self, payload):
        self.payloads.append(payload)
        return True


class _Policy:
    def evaluate_text_observation(self, observation, resolved_settings=None):
        return {'allowed': True, 'reason': 'ok'}


class _Context:
    def build(self, batch):
        return {
            'recent_history': [],
            'channel_summary': batch.channel_name,
            'profile': {'summary': '', 'facts': [{'content': 'likes synthwave'}]},
        }


class _Traces:
    def record_trace(self, *a, **k):
        pass


def _mention_obs():
    return TextMessageObservation(
        observation_id='obs:m1', account_name='alpha', guild_id='g1', guild_name='Guild',
        channel_id='c1', channel_name='general', author_id='u1', username='krem',
        display_name='Krem', message_id='m1', content='hey sapph', clean_content='hey sapph',
        created_at=0.0, is_dm=False, mentioned=True, attachments=[],
    )


def _run(store):
    service = ConversationService(
        event_bridge=_Bridge(), policy_service=_Policy(),
        prompt_context_service=_Context(), trace_repository=_Traces(),
        settings_store=store,
    )
    batching = BatchingService(default_window_seconds=5, typing_extension_seconds=4)
    batching.add_message(_mention_obs())
    service.process_batch(batching.flush_ready(now=10.0)[0])
    return service.event_bridge.payloads


def test_profile_facts_reach_reply_instructions():
    payloads = _run(SettingsStore())
    assert len(payloads) == 1
    instructions = payloads[0].get('reply_instructions') or ''
    assert 'Stored notes about Krem' in instructions
    assert 'likes synthwave' in instructions


def test_memory_toggle_off_drops_profile_hint():
    store = SettingsStore()
    store.global_overlay.profile.update({'enabled': False})
    payloads = _run(store)
    assert len(payloads) == 1
    assert 'Stored notes about' not in (payloads[0].get('reply_instructions') or '')


def test_backfill_migration_fills_names_from_users_cache(tmp_path):
    # Simulate a pre-v9 profile (empty names) + Zeebie's users cache, then
    # re-run migration 10's script — it must fill from the cache, no lookups.
    from plugins.discord.storage.migrations import MIGRATIONS
    repo = _repo(tmp_path)
    conn = repo.sqlite_service.connection()
    conn.execute("INSERT INTO users (user_id, username, display_name) VALUES ('42', 'olduser', 'Old User')")
    repo.get_or_create_profile('alpha', '42')
    conn.commit()
    backfill_sql = dict(MIGRATIONS)[10]
    conn.executescript(backfill_sql)
    row = repo.find_user('alpha', 'Old User')
    assert row and row['user_id'] == '42'
    assert row['username'] == 'olduser'


def test_list_profiles_ships_names_and_birthday(tmp_path):
    # The Memory browser renders from list_profiles — it must carry the name
    # and birthday columns (the SELECT was a stale pre-v9 column list).
    repo = _repo(tmp_path)
    service = ProfileService(profile_repository=repo)
    service.record_interaction('alpha', 'u1', username='krem', display_name='Krem')
    repo.set_birthday('alpha', 'u1', month=3, day=3, channel_id='c1')
    rows = repo.list_profiles('alpha')
    assert rows[0]['display_name'] == 'Krem'
    assert rows[0]['username'] == 'krem'
    assert rows[0]['birthday_month'] == 3


def _tool_runtime(tmp_path, monkeypatch):
    from plugins.discord.tools import discord_tools
    repo = _repo(tmp_path)
    service = ProfileService(profile_repository=repo)

    class _Runtime:
        profile_repository = repo
        profile_service = service
        settings_store = None
        transport = None
    monkeypatch.setattr(discord_tools, 'get_runtime', lambda: _Runtime)
    return discord_tools, repo, service


def test_memory_delete_bound_to_requester_in_discord(tmp_path, monkeypatch):
    tools, repo, service = _tool_runtime(tmp_path, monkeypatch)
    service.record_interaction('alpha', 'victim-1', username='victim')
    service.remember_fact('alpha', 'victim-1', 'loves synthwave music')
    # Hostile channel user (author attacker-2) targets someone else's memory.
    monkeypatch.setattr(tools, '_event_data',
                        lambda: {'account': 'alpha', 'author_id': 'attacker-2'})
    msg, ok = tools.discord_memory(action='delete', user='victim', content='synthwave')
    assert ok is False
    assert len(repo.list_facts('alpha', 'victim-1')) == 1
    # The victim asking about themselves works.
    monkeypatch.setattr(tools, '_event_data',
                        lambda: {'account': 'alpha', 'author_id': 'victim-1'})
    msg, ok = tools.discord_memory(action='delete', user='victim', content='synthwave')
    assert ok is True
    assert repo.list_facts('alpha', 'victim-1') == []


def test_memory_delete_rejects_single_char_match(tmp_path, monkeypatch):
    tools, repo, service = _tool_runtime(tmp_path, monkeypatch)
    service.record_interaction('alpha', 'u1', username='krem')
    service.remember_fact('alpha', 'u1', 'likes synthwave')
    monkeypatch.setattr(tools, '_event_data', lambda: {'account': 'alpha', 'author_id': 'u1'})
    msg, ok = tools.discord_memory(action='delete', user='krem', content='e')
    assert ok is False
    assert len(repo.list_facts('alpha', 'u1')) == 1


def test_memory_account_locked_to_event(tmp_path, monkeypatch):
    tools, repo, service = _tool_runtime(tmp_path, monkeypatch)
    service.record_interaction('private-bot', 'u9', username='secret_friend')
    service.remember_fact('private-bot', 'u9', 'private detail')
    # From a Discord conversation on 'alpha', account= must NOT reach another bot.
    monkeypatch.setattr(tools, '_event_data',
                        lambda: {'account': 'alpha', 'author_id': 'attacker-2'})
    msg, ok = tools.discord_memory(action='search', user='secret_friend', account='private-bot')
    assert 'private detail' not in msg
