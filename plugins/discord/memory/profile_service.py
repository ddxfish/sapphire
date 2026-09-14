"""Per-user profile and relationship management."""

from __future__ import annotations

import time


class ProfileService:
    INTERACTION_DELTA = 0.02

    def __init__(
        self,
        *,
        profile_repository,
        milestone_service=None,
        interest_service=None,
        lore_service=None,
    ):
        self.profile_repository = profile_repository
        self.milestone_service = milestone_service
        self.interest_service = interest_service
        self.lore_service = lore_service

    def remember_fact(self, account_name: str, user_id: str, content: str, *, source: str = 'explicit',
                      confidence: float = 1.0, origin: str = '') -> int:
        self.profile_repository.get_or_create_profile(account_name, user_id)
        return self.profile_repository.add_fact(
            account_name, user_id, content, source=source, confidence=confidence, origin=origin,
        )

    def list_facts(
        self,
        account_name: str,
        user_id: str,
        *,
        limit: int = 50,
        include_forgotten: bool = False,
    ) -> list[dict]:
        return self.profile_repository.list_facts(
            account_name, user_id, limit=limit, include_forgotten=include_forgotten,
        )

    def list_review_facts(
        self,
        account_name: str,
        *,
        source: str = 'ambient_distill',
        pending_only: bool = True,
        limit: int = 40,
    ) -> list[dict]:
        return self.profile_repository.list_recent_facts(
            account_name,
            source=source,
            pending_only=pending_only,
            limit=limit,
        )

    def update_fact(self, fact_id: int, content: str) -> dict | None:
        text = str(content or '').strip()
        if not text:
            raise ValueError('content required')
        return self.profile_repository.update_fact_content(fact_id, text)

    def pin_fact(self, fact_id: int, pinned: bool = True) -> dict | None:
        return self.profile_repository.set_fact_pinned(fact_id, pinned)

    def soft_forget_fact(self, fact_id: int) -> dict | None:
        """Hide one fact from recall without wiping the user."""
        return self.profile_repository.soft_forget_fact(fact_id)

    def restore_fact(self, fact_id: int) -> dict | None:
        return self.profile_repository.restore_fact(fact_id)

    def record_interaction(
        self,
        account_name: str,
        user_id: str,
        *,
        username: str = '',
        display_name: str = '',
        positive: bool = True,
        message_text: str = '',
        now: float | None = None,
        origin: str = '',
    ) -> dict:
        profile = self.profile_repository.get_or_create_profile(account_name, user_id)
        now_ts = float(now if now is not None else time.time())
        previous_last = float(profile.get('last_interaction_at') or 0.0)
        first_seen = float(profile.get('first_seen_at') or 0.0)
        delta = self.INTERACTION_DELTA if positive else -self.INTERACTION_DELTA
        new_count = int(profile['message_count']) + 1
        fields = dict(
            fondness=min(1.0, max(0.0, profile['fondness'] + delta)),
            familiarity=min(1.0, profile['familiarity'] + 0.01),
            message_count=new_count,
            last_interaction_at=now_ts,
        )
        if first_seen <= 0:
            fields['first_seen_at'] = now_ts
        # Keep live names on the profile — the Memory browser and name-based
        # tool lookups need something better than a raw snowflake.
        if username:
            fields['username'] = str(username)
        if display_name:
            fields['display_name'] = str(display_name)
        updated = self.profile_repository.update_profile(account_name, user_id, **fields)

        if self.milestone_service:
            self.milestone_service.observe_interaction(
                account_name,
                user_id,
                message_count=new_count,
                previous_last_interaction_at=previous_last,
                now=now_ts,
            )
        if self.interest_service and message_text:
            self.interest_service.observe_message(account_name, user_id, message_text, origin=origin)
        return updated

    def build_context(
        self,
        account_name: str,
        user_id: str,
        *,
        guild_id: str = '',
        channel_id: str = '',
        is_dm: bool = False,
    ) -> dict:
        """In a server, facts and interests learned in DMs stay out of the
        prompt (H16b); in a DM she may draw on everything."""
        profile = self.profile_repository.get_or_create_profile(account_name, user_id)
        for_guild = None if is_dm else (str(guild_id or '') or None)
        facts = self.profile_repository.list_facts(account_name, user_id, limit=12, for_guild=for_guild)
        context = {
            'summary': profile.get('summary') or '',
            'facts': facts,
            'relationship': {
                'fondness': float(profile.get('fondness') or 0.0),
                'familiarity': float(profile.get('familiarity') or 0.0),
                'interest': float(profile.get('interest') or 0.5),
                'patience': float(profile.get('patience') or 0.5),
                'trust': float(profile.get('trust') or 0.5),
                'message_count': int(profile.get('message_count') or 0),
                'first_seen_at': float(profile.get('first_seen_at') or 0.0),
                'last_interaction_at': float(profile.get('last_interaction_at') or 0.0),
            },
            'milestones': [],
            'interests': [],
            'lore': [],
        }
        if self.milestone_service:
            context['milestones'] = self.milestone_service.pending_for_prompt(account_name, user_id)
        if self.interest_service:
            context['interests'] = self.interest_service.top_topics(
                account_name, user_id, limit=6, exclude_dm=not is_dm,
            )
        if self.lore_service and guild_id:
            context['lore'] = self.lore_service.build_context(
                account_name, guild_id=guild_id, channel_id=channel_id, limit=6,
            )
        return context

    def acknowledge_milestones(self, milestone_ids: list[int]) -> int:
        if not self.milestone_service:
            return 0
        return self.milestone_service.acknowledge(milestone_ids)

    def forget_user(self, account_name: str, user_id: str) -> None:
        self.profile_repository.forget_user(account_name, user_id)

    def list_profiles(self, account_name: str, limit: int = 50) -> list[dict]:
        return self.profile_repository.list_profiles(account_name, limit=limit)
