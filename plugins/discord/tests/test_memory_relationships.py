"""Relationship milestones, server lore, interest graphs, and fact soft-forget."""

from plugins.discord.conversation.conversation_service import _profile_prompt_hint
from plugins.discord.memory.interest_service import InterestService, extract_topics
from plugins.discord.memory.lore_service import LoreService
from plugins.discord.memory.milestone_service import MilestoneService, DEFAULT_RETURN_GAP_SECONDS
from plugins.discord.memory.profile_service import ProfileService
from plugins.discord.models.settings import SettingsStore
from plugins.discord.storage.repositories.interests import InterestRepository
from plugins.discord.storage.repositories.lore import LoreRepository
from plugins.discord.storage.repositories.messages import MessageRepository
from plugins.discord.storage.repositories.milestones import MilestoneRepository
from plugins.discord.storage.repositories.profiles import ProfileRepository
from plugins.discord.storage.sqlite import SQLiteService
from plugins.discord.runtime.retention_service import RetentionService


def _stack(tmp_path):
    sqlite = SQLiteService(tmp_path / 'memory_rel.sqlite3')
    sqlite.start()
    profiles = ProfileRepository(sqlite)
    milestones = MilestoneRepository(sqlite)
    lore = LoreRepository(sqlite)
    interests = InterestRepository(sqlite)
    milestone_service = MilestoneService(milestone_repository=milestones)
    lore_service = LoreService(lore_repository=lore)
    interest_service = InterestService(interest_repository=interests)
    profile_service = ProfileService(
        profile_repository=profiles,
        milestone_service=milestone_service,
        interest_service=interest_service,
        lore_service=lore_service,
    )
    return {
        'sqlite': sqlite,
        'profiles': profiles,
        'milestones': milestones,
        'lore': lore,
        'interests': interests,
        'milestone_service': milestone_service,
        'lore_service': lore_service,
        'interest_service': interest_service,
        'profile_service': profile_service,
    }


def test_migration_creates_relationship_tables(tmp_path):
    stack = _stack(tmp_path)
    version = stack['sqlite'].connection().execute('SELECT version FROM schema_version').fetchone()[0]
    assert version >= 11
    tables = {
        row[0]
        for row in stack['sqlite'].connection().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert 'relationship_milestones' in tables
    assert 'server_lore' in tables
    assert 'interest_topics' in tables


def test_first_chat_and_conversation_count_milestones(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    now = 1_700_000_000.0
    service.record_interaction('alpha', 'u1', username='alice', now=now)
    miles = stack['milestone_service'].recent_for_user('alpha', 'u1')
    assert any(m['milestone_type'] == 'first_chat' for m in miles)

    for i in range(9):
        service.record_interaction('alpha', 'u1', now=now + i + 1)
    miles = stack['milestone_service'].recent_for_user('alpha', 'u1')
    assert any(m['milestone_key'] == 'count:10' for m in miles)


def test_return_after_gap_milestone(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    now = 1_700_000_000.0
    service.record_interaction('alpha', 'u1', now=now)
    gap = DEFAULT_RETURN_GAP_SECONDS + 3600
    created = stack['milestone_service'].observe_interaction(
        'alpha',
        'u1',
        message_count=2,
        previous_last_interaction_at=now,
        now=now + gap,
    )
    # record_interaction would also bump count; call observe directly for gap focus
    assert any(m['milestone_type'] == 'return_after_gap' for m in created)


def test_server_lore_scoped_and_soft_forget(tmp_path):
    stack = _stack(tmp_path)
    lore = stack['lore_service']
    lore.remember('alpha', 'Deploy day is Thursday', guild_id='g1', pinned=True)
    lore.remember('alpha', 'Only in #ops', guild_id='g1', channel_id='c-ops')
    lore.remember('alpha', 'Other guild lore', guild_id='g2')

    ctx = lore.build_context('alpha', guild_id='g1', channel_id='c-ops')
    contents = [row['content'] for row in ctx]
    assert 'Deploy day is Thursday' in contents
    assert 'Only in #ops' in contents
    assert 'Other guild lore' not in contents

    row_id = stack['lore'].list_lore('alpha', guild_id='g1')[0]['id']
    stack['lore_service'].soft_forget(row_id)
    active = lore.build_context('alpha', guild_id='g1')
    assert all(r['id'] != row_id for r in active)
    forgotten = lore.list_lore('alpha', include_forgotten=True)
    assert any(r['id'] == row_id and r['forgotten'] == 1 for r in forgotten)


def test_interest_extraction_and_outreach_hint(tmp_path):
    assert 'coding' in extract_topics('I love coding in python')
    assert 'gaming' in extract_topics('playing on steam tonight #gaming')

    stack = _stack(tmp_path)
    interests = stack['interest_service']
    interests.observe_message('alpha', 'u1', 'Been coding and gaming on steam')
    topics = interests.top_topics('alpha', 'u1')
    names = {t['topic'] for t in topics}
    assert 'coding' in names
    assert 'gaming' in names
    hint = interests.outreach_hint(topics)
    assert 'coding' in hint or 'gaming' in hint


def test_profile_soft_forget_and_pin_facts(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    service.remember_fact('alpha', 'u1', 'likes tea')
    service.remember_fact('alpha', 'u1', 'has a cat')
    facts = service.list_facts('alpha', 'u1')
    tea = next(f for f in facts if 'tea' in f['content'])
    service.pin_fact(tea['id'])
    service.soft_forget_fact(next(f for f in facts if 'cat' in f['content'])['id'])

    active = service.list_facts('alpha', 'u1')
    assert len(active) == 1
    assert active[0]['pinned'] == 1
    assert 'tea' in active[0]['content']

    all_facts = service.list_facts('alpha', 'u1', include_forgotten=True)
    assert len(all_facts) == 2
    ctx = service.build_context('alpha', 'u1')
    assert all('cat' not in f['content'] for f in ctx['facts'])


def test_distill_review_queue_pending_and_pin(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    service.record_interaction('alpha', 'u1', username='alice', display_name='Alice')
    service.remember_fact('alpha', 'u1', 'Works night shifts', source='ambient_distill')
    service.remember_fact('alpha', 'u1', 'Has a dog named Mochi', source='ambient_distill')
    service.remember_fact('alpha', 'u1', 'Operator note', source='operator')
    pending = service.list_review_facts('alpha', pending_only=True)
    assert len(pending) == 2
    assert all(f['source'] == 'ambient_distill' for f in pending)
    assert all(int(f['pinned'] or 0) == 0 for f in pending)
    assert any(f.get('display_name') == 'Alice' or f.get('username') == 'alice' for f in pending)

    service.pin_fact(pending[0]['id'])
    still_pending = service.list_review_facts('alpha', pending_only=True)
    assert len(still_pending) == 1
    with_pinned = service.list_review_facts('alpha', pending_only=False)
    assert len(with_pinned) == 2


def test_build_context_includes_lore_milestones_interests(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    service.record_interaction(
        'alpha', 'u1', username='alice', message_text='I enjoy music and anime', now=1_700_000_000.0,
    )
    stack['lore_service'].remember('alpha', 'Deploy day is Thursday', guild_id='g1')
    ctx = service.build_context('alpha', 'u1', guild_id='g1', channel_id='c1')
    assert ctx['milestones']
    assert any(t['topic'] in ('music', 'anime') for t in ctx['interests'])
    assert any('Deploy day' in row['content'] for row in ctx['lore'])

    hint = _profile_prompt_hint(ctx, 'Alice')
    assert 'Deploy day' in hint
    assert 'Alice' in hint
    assert 'Topics they talk about' in hint


def test_forget_user_clears_milestones_and_interests(tmp_path):
    stack = _stack(tmp_path)
    service = stack['profile_service']
    service.record_interaction('alpha', 'u1', message_text='gaming session', now=1_700_000_000.0)
    assert stack['milestone_service'].recent_for_user('alpha', 'u1')
    assert stack['interest_service'].top_topics('alpha', 'u1')

    retention = RetentionService(sqlite_service=stack['sqlite'])
    retention.forget_user(
        'alpha',
        'u1',
        profile_repository=stack['profiles'],
        milestone_repository=stack['milestones'],
        interest_repository=stack['interests'],
    )
    assert stack['milestone_service'].recent_for_user('alpha', 'u1') == []
    assert stack['interest_service'].top_topics('alpha', 'u1') == []
    assert service.build_context('alpha', 'u1')['facts'] == []


def test_milestone_acknowledge_clears_pending(tmp_path):
    stack = _stack(tmp_path)
    stack['profile_service'].record_interaction('alpha', 'u1', now=1_700_000_000.0)
    pending = stack['milestone_service'].pending_for_prompt('alpha', 'u1')
    assert pending
    stack['profile_service'].acknowledge_milestones([pending[0]['id']])
    assert stack['milestone_service'].pending_for_prompt('alpha', 'u1') == []
