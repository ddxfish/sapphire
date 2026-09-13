"""Ambient chat buffering and distill gating."""

from types import SimpleNamespace

from plugins.discord.memory.distill_service import DistillService, _parse_fact_list
from plugins.discord.models.settings import ProfileSettings, SettingsStore
from plugins.discord.sapphire.llm_settings import distill_llm_from_settings
from plugins.discord.storage.repositories.profile_buffers import ProfileBufferRepository
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService


def _stack(tmp_path):
    sqlite = SQLiteService(tmp_path / 'distill.sqlite3')
    sqlite.start()
    buffers = ProfileBufferRepository(sqlite)
    profiles = ProfileRepository(sqlite)
    service = DistillService(
        buffer_repository=buffers,
        profile_repository=profiles,
        sqlite_service=sqlite,
    )
    return sqlite, buffers, profiles, service


def _obs(**kwargs):
    base = dict(
        account_name='alpha',
        author_id='u1',
        clean_content='I have a dog named Mochi and walk him each morning',
        channel_name='general',
        author_is_bot=False,
        created_at=1_700_000_000.0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _settings(**profile_kwargs):
    store = SettingsStore()
    store.global_overlay.profile.update(profile_kwargs)
    return store.resolve()


def test_parse_fact_list_handles_fences_and_objects():
    assert _parse_fact_list('["Has a dog named Mochi"]', max_facts=3) == ['Has a dog named Mochi']
    assert _parse_fact_list('```json\n{"facts":["Works nights"]}\n```', max_facts=3) == ['Works nights']


def test_distill_llm_inherits_reply_model():
    store = SettingsStore()
    store.global_overlay.cognitive.update(llm_primary='ollama', llm_model='llama3.2')
    settings = store.resolve()
    assert distill_llm_from_settings(settings) == ('ollama', 'llama3.2')
    store.global_overlay.profile.update(distill_model_provider='openai', distill_model_name='gpt-4o-mini')
    settings = store.resolve()
    assert distill_llm_from_settings(settings) == ('openai', 'gpt-4o-mini')


def test_buffer_requires_opt_in(tmp_path):
    _, buffers, _, service = _stack(tmp_path)
    assert service.buffer_observation(_obs(), _settings(enabled=True, ambient_distill_enabled=False)) is False
    assert buffers.pending_count('alpha') == 0
    assert service.buffer_observation(_obs(), _settings(enabled=True, ambient_distill_enabled=True)) is True
    assert buffers.pending_count('alpha', 'u1') == 1


def test_buffer_skips_bots_and_short_text(tmp_path):
    _, buffers, _, service = _stack(tmp_path)
    settings = _settings(enabled=True, ambient_distill_enabled=True)
    assert service.buffer_observation(_obs(author_is_bot=True), settings) is False
    assert service.buffer_observation(_obs(clean_content='hi'), settings) is False
    assert buffers.pending_count('alpha') == 0


def test_run_skips_when_disabled_or_interval(tmp_path):
    _, buffers, profiles, service = _stack(tmp_path)
    settings = _settings(
        enabled=True,
        ambient_distill_enabled=True,
        ambient_distill_interval_hours=1.0,
        ambient_distill_min_messages=2,
    )
    for i in range(3):
        buffers.add('alpha', 'u1', f'I like coding session {i} and python projects')
    service._call_llm = lambda *a, **k: ['Likes coding in python']  # noqa: E731

    first = service.run_for_account('alpha', settings, force=False)
    assert first['status'] == 'ok'
    assert first['facts_added'] == 1
    assert profiles.list_facts('alpha', 'u1')[0]['source'] == 'ambient_distill'
    assert buffers.pending_count('alpha', 'u1') == 0

    buffers.add('alpha', 'u1', 'Also has a cat named Pixel who naps a lot')
    buffers.add('alpha', 'u1', 'Night shifts most weekdays for work')
    second = service.run_for_account('alpha', settings, force=False)
    assert second['status'] == 'skipped'
    assert second['reason'] == 'interval_not_elapsed'

    service._call_llm = lambda *a, **k: ['Has a cat named Pixel', 'Works night shifts']  # noqa: E731
    forced = service.run_for_account('alpha', settings, force=True, user_id='u1')
    assert forced['status'] == 'ok'
    assert forced['facts_added'] >= 1


def test_run_respects_min_messages(tmp_path):
    _, buffers, _, service = _stack(tmp_path)
    settings = _settings(
        enabled=True,
        ambient_distill_enabled=True,
        ambient_distill_min_messages=5,
    )
    buffers.add('alpha', 'u1', 'Only one durable sounding line about my dog Mochi')
    service._call_llm = lambda *a, **k: ['Has a dog named Mochi']  # noqa: E731
    result = service.run_for_account('alpha', settings, force=False)
    assert result['status'] == 'ok'
    assert result['facts_added'] == 0
    assert result['results'][0]['reason'] == 'below_min_messages'


def test_parse_fact_list_survives_junk_replies():
    # H9 (hunt 2026-09-12): the first-'['…last-']' scavenge + unconditional
    # mark_processed ate buffers on think-block echoes and persisted junk.
    # think block echoing a channel mention, then a legit empty answer
    assert _parse_fact_list('<think>the snippet says [#general] hi</think>\n[]', max_facts=3) == []
    # think-only / truncated → parse failure, NOT an empty answer
    assert _parse_fact_list('<think>still thinking about [#general', max_facts=3) is None
    assert _parse_fact_list("I can't help with that.", max_facts=3) is None
    assert _parse_fact_list('', max_facts=3) is None
    # placeholder echo of the format hint is not a fact
    assert _parse_fact_list('["<fact>", "<fact>"]', max_facts=3) == []
    # non-string items never persist as their str()
    assert _parse_fact_list('[true, 42.5, ["a", "b"], "Plays bass"]', max_facts=5) == ['Plays bass']
    # the LAST array wins over a bracketed preamble
    assert _parse_fact_list('Notes: [#general] chatter. Facts: ["Plays bass"]', max_facts=3) == ['Plays bass']


def test_parse_failure_leaves_buffers_pending(tmp_path):
    # H9: a reply with no JSON array used to mark every buffer processed with a
    # green "+0 facts" trace. Now the snippets stay pending for the next run.
    _, buffers, profiles, service = _stack(tmp_path)
    settings = _settings(enabled=True, ambient_distill_enabled=True, ambient_distill_min_messages=1)
    buffers.add('alpha', 'u1', 'I have a dog named Mochi and walk him each morning')
    buffers.add('alpha', 'u1', 'Night shifts most weekdays for work')
    service._call_llm = lambda *a, **k: None  # noqa: E731 — refusal / think-only / truncated

    result = service.run_for_account('alpha', settings, force=True, user_id='u1')

    assert result['results'][0]['status'] == 'parse_failed'
    assert result['facts_added'] == 0
    assert buffers.pending_count('alpha', 'u1') == 2
    assert profiles.list_facts('alpha', 'u1') == []
