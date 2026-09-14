"""Profile, lore, milestone, and interest visibility/edit routes."""

from __future__ import annotations

from plugins.discord.daemon import get_runtime


def _account_from_request(runtime, query=None, body=None) -> str:
    query = query or {}
    body = body or {}
    account_name = str(query.get('account') or body.get('account') or body.get('account_name') or '').strip()
    if not account_name and runtime.transport:
        connected = runtime.transport.list_connected()
        if connected:
            account_name = connected[0]
    return account_name


def list_profiles(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'profiles': []}
    query = kwargs.get('query') or {}
    account_name = _account_from_request(runtime, query=query)
    if not account_name:
        return {'profiles': []}
    profiles = runtime.profile_service.list_profiles(account_name, limit=50)
    return {'profiles': profiles}


def list_profile_facts(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'facts': []}
    query = kwargs.get('query') or {}
    account_name = str(query.get('account') or '').strip()
    user_id = str(query.get('user') or '').strip()
    include_forgotten = str(query.get('include_forgotten') or '').lower() in ('1', 'true', 'yes')
    if not account_name or not user_id:
        return {'facts': []}
    return {
        'facts': runtime.profile_service.list_facts(
            account_name, user_id, limit=50, include_forgotten=include_forgotten,
        ),
    }


def list_distill_review(**kwargs):
    """Pending ambient_distill facts for operator pin / soft-forget."""
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'facts': [], 'account': ''}
    query = kwargs.get('query') or {}
    account_name = _account_from_request(runtime, query=query)
    if not account_name:
        return {'facts': [], 'account': ''}
    source = str(query.get('source') or 'ambient_distill').strip() or 'ambient_distill'
    pending_only = str(query.get('pending_only') or '1').lower() not in ('0', 'false', 'no')
    try:
        limit = int(query.get('limit', 40))
    except (TypeError, ValueError):
        limit = 40
    facts = runtime.profile_service.list_review_facts(
        account_name, source=source, pending_only=pending_only, limit=limit,
    )
    return {'facts': facts, 'account': account_name, 'source': source, 'pending_only': pending_only}


def update_profile_fact(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    fact_id = int(body.get('fact_id') or 0)
    if not fact_id:
        return {'error': 'fact_id required'}
    action = str(body.get('action') or 'edit').strip().lower()
    if action == 'pin':
        fact = runtime.profile_service.pin_fact(fact_id, True)
    elif action == 'unpin':
        fact = runtime.profile_service.pin_fact(fact_id, False)
    elif action == 'soft_forget':
        fact = runtime.profile_service.soft_forget_fact(fact_id)
    elif action == 'restore':
        fact = runtime.profile_service.restore_fact(fact_id)
    elif action == 'edit':
        content = str(body.get('content') or '').strip()
        if not content:
            return {'error': 'content required for edit'}
        fact = runtime.profile_service.update_fact(fact_id, content)
    else:
        return {'error': f'unknown action {action!r}'}
    if not fact:
        return {'error': 'fact not found'}
    return {'fact': fact}


def add_profile_fact(**kwargs):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    account_name = _account_from_request(runtime, body=body)
    user_id = str(body.get('user_id') or body.get('user') or '').strip()
    content = str(body.get('content') or '').strip()
    if not account_name or not user_id or not content:
        return {'error': 'account, user_id, and content required'}
    fact_id = runtime.profile_service.remember_fact(
        account_name, user_id, content, source=str(body.get('source') or 'operator'),
    )
    return {'fact_id': fact_id}


def list_milestones(**kwargs):
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'milestone_service', None):
        return {'milestones': []}
    query = kwargs.get('query') or {}
    account_name = str(query.get('account') or '').strip()
    user_id = str(query.get('user') or '').strip()
    if not account_name or not user_id:
        return {'milestones': []}
    return {
        'milestones': runtime.milestone_service.recent_for_user(account_name, user_id, limit=30),
    }


def list_interests(**kwargs):
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'interest_service', None):
        return {'interests': []}
    query = kwargs.get('query') or {}
    account_name = str(query.get('account') or '').strip()
    user_id = str(query.get('user') or '').strip()
    if not account_name or not user_id:
        return {'interests': []}
    return {
        'interests': runtime.interest_service.top_topics(account_name, user_id, limit=20),
    }


def list_lore(**kwargs):
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'lore_service', None):
        return {'lore': []}
    query = kwargs.get('query') or {}
    account_name = _account_from_request(runtime, query=query)
    if not account_name:
        return {'lore': []}
    guild_id = query.get('guild')
    include_forgotten = str(query.get('include_forgotten') or '').lower() in ('1', 'true', 'yes')
    return {
        'lore': runtime.lore_service.list_lore(
            account_name,
            guild_id=str(guild_id) if guild_id is not None and str(guild_id) != '' else None,
            include_forgotten=include_forgotten,
            limit=100,
        ),
    }


def mutate_lore(**kwargs):
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'lore_service', None):
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    action = str(body.get('action') or 'add').strip().lower()
    account_name = _account_from_request(runtime, body=body)

    if action == 'add':
        content = str(body.get('content') or '').strip()
        if not account_name or not content:
            return {'error': 'account and content required'}
        lore_id = runtime.lore_service.remember(
            account_name,
            content,
            guild_id=str(body.get('guild_id') or ''),
            channel_id=str(body.get('channel_id') or ''),
            source=str(body.get('source') or 'operator'),
            pinned=bool(body.get('pinned')),
        )
        return {'lore_id': lore_id}

    lore_id = int(body.get('lore_id') or 0)
    if not lore_id:
        return {'error': 'lore_id required'}
    if action == 'edit':
        content = str(body.get('content') or '').strip()
        if not content:
            return {'error': 'content required'}
        row = runtime.lore_service.update(lore_id, content)
    elif action == 'pin':
        row = runtime.lore_service.set_pinned(lore_id, True)
    elif action == 'unpin':
        row = runtime.lore_service.set_pinned(lore_id, False)
    elif action == 'soft_forget':
        row = runtime.lore_service.soft_forget(lore_id)
    elif action == 'restore':
        row = runtime.lore_service.restore(lore_id)
    elif action == 'delete':
        ok = runtime.lore_service.delete(lore_id)
        return {'deleted': ok}
    else:
        return {'error': f'unknown action {action!r}'}
    if not row:
        return {'error': 'lore not found'}
    return {'lore': row}


SCRATCH_USER_PREFIX = 'test:'


def _scratch_user(user_id: str) -> str:
    """The test route never touches a real member's counters (M35): whatever id
    the operator types is mapped onto a scratch profile named after it."""
    user_id = str(user_id or '').strip()
    if not user_id or user_id.startswith(SCRATCH_USER_PREFIX):
        return user_id
    return SCRATCH_USER_PREFIX + user_id


def memory_test(**kwargs):
    """Operator test helpers for milestones, lore, and interest graphs.
    Every write lands on a `test:`-prefixed scratch user (see _scratch_user)."""
    runtime = get_runtime()
    if not runtime or not runtime.profile_service:
        return {'error': 'Runtime not available'}
    body = kwargs.get('body') or {}
    kind = str(body.get('kind') or '').strip().lower()
    account_name = _account_from_request(runtime, body=body)
    user_id = _scratch_user(body.get('user_id') or body.get('user') or '')
    if not account_name:
        return {'error': 'account required'}

    if kind == 'milestone':
        if not user_id:
            return {'error': 'user_id required'}
        runtime.profile_repository.get_or_create_profile(account_name, user_id)
        row = runtime.milestone_service.seed_test_milestone(
            account_name,
            user_id,
            detail=str(body.get('detail') or 'Test relationship milestone'),
        )
        return {'kind': kind, 'milestone': row}

    if kind == 'simulate_interaction':
        if not user_id:
            return {'error': 'user_id required'}
        count = max(1, min(20, int(body.get('count') or 1)))
        gap_days = float(body.get('gap_days') or 0)
        now = __import__('time').time()
        results = []
        for i in range(count):
            # First call can pretend the previous interaction was long ago.
            if i == 0 and gap_days > 0:
                profile = runtime.profile_repository.get_or_create_profile(account_name, user_id)
                runtime.profile_repository.update_profile(
                    account_name,
                    user_id,
                    last_interaction_at=now - (gap_days * 86400),
                )
                del profile
            updated = runtime.profile_service.record_interaction(
                account_name,
                user_id,
                username=str(body.get('username') or 'testuser'),
                display_name=str(body.get('display_name') or 'Test User'),
                message_text=str(body.get('message_text') or 'I love coding and gaming on steam'),
                now=now + i,
            )
            results.append({
                'message_count': updated.get('message_count'),
                'last_interaction_at': updated.get('last_interaction_at'),
            })
        milestones = runtime.milestone_service.recent_for_user(account_name, user_id, limit=10)
        interests = runtime.interest_service.top_topics(account_name, user_id, limit=10)
        return {
            'kind': kind,
            'interactions': results,
            'milestones': milestones,
            'interests': interests,
        }

    if kind == 'lore':
        content = str(body.get('content') or 'Deploy day is Thursday').strip()
        lore_id = runtime.lore_service.remember(
            account_name,
            content,
            guild_id=str(body.get('guild_id') or 'test-guild'),
            channel_id=str(body.get('channel_id') or ''),
            source='test',
            pinned=bool(body.get('pinned', True)),
        )
        return {
            'kind': kind,
            'lore_id': lore_id,
            'lore': runtime.lore_service.list_lore(account_name, limit=20),
        }

    if kind == 'interest':
        if not user_id:
            return {'error': 'user_id required'}
        topic = str(body.get('topic') or 'coding').strip()
        row = runtime.interest_service.seed_topic(account_name, user_id, topic)
        topics = runtime.interest_service.top_topics(account_name, user_id, limit=10)
        hint = runtime.interest_service.outreach_hint(topics)
        return {'kind': kind, 'seeded': row, 'interests': topics, 'outreach_hint': hint}

    if kind == 'outreach_hint':
        channel_id = str(body.get('channel_id') or '').strip()
        if not channel_id:
            return {'error': 'channel_id required'}
        topics, hint = runtime.outreach_service._interest_context(account_name, channel_id) if runtime.outreach_service else ([], '')
        return {'kind': kind, 'topics': topics, 'outreach_hint': hint}

    if kind == 'context_preview':
        if not user_id:
            return {'error': 'user_id required'}
        context = runtime.profile_service.build_context(
            account_name,
            user_id,
            guild_id=str(body.get('guild_id') or ''),
            channel_id=str(body.get('channel_id') or ''),
        )
        return {'kind': kind, 'context': context}

    if kind == 'ambient_distill':
        if not getattr(runtime, 'distill_service', None):
            return {'error': 'Distill service unavailable'}
        settings = runtime.settings_store.resolve() if getattr(runtime, 'settings_store', None) else None
        if not settings:
            return {'error': 'Settings unavailable'}
        # Optional: seed a few buffer lines for the user so the test is useful offline.
        seed_text = str(body.get('seed_text') or '').strip()
        if user_id and seed_text and runtime.profile_buffer_repository:
            for line in seed_text.split('\n'):
                line = line.strip()
                if line:
                    runtime.profile_buffer_repository.add(account_name, user_id, line)
        result = runtime.distill_service.run_for_account(
            account_name,
            settings,
            force=True,
            user_id=user_id,
        )
        pending = 0
        if runtime.profile_buffer_repository:
            pending = runtime.profile_buffer_repository.pending_count(
                account_name, user_id or None,
            )
        return {'kind': kind, 'result': result, 'pending_buffer': pending}

    return {'error': f'unknown kind {kind!r}; use milestone|simulate_interaction|lore|interest|outreach_hint|context_preview|ambient_distill'}
