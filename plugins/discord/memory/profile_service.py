"""Per-user profile and relationship management."""

from __future__ import annotations

from plugins.discord.models.profiles import AgentAffect, RelationshipState


class ProfileService:
    INTERACTION_DELTA = 0.02

    def __init__(self, *, profile_repository):
        self.profile_repository = profile_repository

    def remember_fact(self, account_name: str, user_id: str, content: str, *, source: str = 'explicit', confidence: float = 1.0) -> int:
        self.profile_repository.get_or_create_profile(account_name, user_id)
        return self.profile_repository.add_fact(account_name, user_id, content, source=source, confidence=confidence)

    def record_interaction(self, account_name: str, user_id: str, *, username: str = '', positive: bool = True) -> dict:
        profile = self.profile_repository.get_or_create_profile(account_name, user_id)
        delta = self.INTERACTION_DELTA if positive else -self.INTERACTION_DELTA
        return self.profile_repository.update_profile(
            account_name,
            user_id,
            fondness=min(1.0, max(0.0, profile['fondness'] + delta)),
            familiarity=min(1.0, profile['familiarity'] + 0.01),
            message_count=int(profile['message_count']) + 1,
        )

    def buffer_message(self, account_name: str, user_id: str, content: str) -> int:
        return self.profile_repository.buffer_message(account_name, user_id, content)

    def build_context(self, account_name: str, user_id: str) -> dict:
        profile = self.profile_repository.get_or_create_profile(account_name, user_id)
        facts = self.profile_repository.list_facts(account_name, user_id, limit=10)
        return {
            'summary': profile.get('summary') or '',
            'facts': facts,
            'relationship': self.profile_repository.relationship_from_row(profile).to_dict(),
        }

    def forget_user(self, account_name: str, user_id: str) -> None:
        self.profile_repository.forget_user(account_name, user_id)

    def get_affect(self, account_name: str) -> AgentAffect:
        return self.profile_repository.get_affect(account_name)

    def adjust_affect(self, account_name: str, **deltas) -> AgentAffect:
        affect = self.get_affect(account_name)
        payload = affect.to_dict()
        for key, delta in deltas.items():
            if key in payload:
                payload[key] = min(1.0, max(0.0, payload[key] + float(delta)))
        updated = AgentAffect.from_dict(payload)
        return self.profile_repository.save_affect(account_name, updated)

    def list_profiles(self, account_name: str, limit: int = 50) -> list[dict]:
        return self.profile_repository.list_profiles(account_name, limit=limit)

    def apply_distillation(self, account_name: str, user_id: str, result: dict) -> dict:
        profile = self.profile_repository.get_or_create_profile(account_name, user_id)
        summary = result.get('summary') or profile.get('summary') or ''
        updates = {'summary': summary}
        disposition = result.get('disposition') or {}
        for key in ('fondness', 'trust', 'patience', 'respect', 'interest', 'familiarity'):
            if key in disposition:
                current = float(profile.get(key, 0.5))
                updates[key] = min(1.0, max(0.0, current + float(disposition[key])))
        updated = self.profile_repository.update_profile(account_name, user_id, **updates)
        for fact in result.get('facts') or []:
            content = fact.get('content')
            if content:
                self.profile_repository.add_fact(
                    account_name,
                    user_id,
                    content,
                    source='distilled',
                    confidence=float(fact.get('confidence', 0.7)),
                )
        return updated
