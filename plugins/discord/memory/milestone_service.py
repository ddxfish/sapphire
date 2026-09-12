"""Detect and surface relationship milestones for people the bot knows."""

from __future__ import annotations

import time
from datetime import datetime, timezone

# Conversation-count thresholds (message_count after the interaction).
CONVERSATION_THRESHOLDS = (1, 10, 25, 50, 100, 250, 500, 1000)

# Gap in seconds before a return is noteworthy (14 days).
DEFAULT_RETURN_GAP_SECONDS = 14 * 86400


class MilestoneService:
    def __init__(
        self,
        *,
        milestone_repository,
        return_gap_seconds: float = DEFAULT_RETURN_GAP_SECONDS,
    ):
        self.milestone_repository = milestone_repository
        self.return_gap_seconds = float(return_gap_seconds)

    def observe_interaction(
        self,
        account_name: str,
        user_id: str,
        *,
        message_count: int,
        previous_last_interaction_at: float = 0.0,
        now: float | None = None,
    ) -> list[dict]:
        """Record any milestones triggered by this interaction. Returns new rows."""
        now_ts = float(now if now is not None else time.time())
        created: list[dict] = []
        count = int(message_count)

        if count == 1:
            row = self.milestone_repository.record(
                account_name,
                user_id,
                milestone_type='first_chat',
                milestone_key='first_chat',
                detail='First conversation together',
                created_at=now_ts,
            )
            if row:
                created.append(row)
        elif count in CONVERSATION_THRESHOLDS:
            row = self.milestone_repository.record(
                account_name,
                user_id,
                milestone_type='conversation_count',
                milestone_key=f'count:{count}',
                detail=f'{count} conversations together',
                created_at=now_ts,
            )
            if row:
                created.append(row)

        prev = float(previous_last_interaction_at or 0.0)
        if prev > 0 and (now_ts - prev) >= self.return_gap_seconds:
            gap_days = int((now_ts - prev) / 86400)
            day_key = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime('%Y-%m-%d')
            row = self.milestone_repository.record(
                account_name,
                user_id,
                milestone_type='return_after_gap',
                milestone_key=f'return:{day_key}',
                detail=f'Returned after {gap_days} days away',
                created_at=now_ts,
            )
            if row:
                created.append(row)

        return created

    def pending_for_prompt(self, account_name: str, user_id: str, *, limit: int = 3) -> list[dict]:
        return self.milestone_repository.list_for_user(
            account_name, user_id, limit=limit, unacknowledged_only=True,
        )

    def recent_for_user(self, account_name: str, user_id: str, *, limit: int = 20) -> list[dict]:
        return self.milestone_repository.list_for_user(account_name, user_id, limit=limit)

    def acknowledge(self, milestone_ids: list[int]) -> int:
        return self.milestone_repository.acknowledge_many(milestone_ids)

    def seed_test_milestone(
        self,
        account_name: str,
        user_id: str,
        *,
        milestone_type: str = 'conversation_count',
        detail: str = 'Test milestone',
    ) -> dict | None:
        """Operator test helper — insert a unique unacknowledged milestone."""
        key = f'test:{int(time.time() * 1000)}'
        return self.milestone_repository.record(
            account_name,
            user_id,
            milestone_type=milestone_type,
            milestone_key=key,
            detail=detail,
        )
