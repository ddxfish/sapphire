"""Authoritative world-model read/write service."""

from __future__ import annotations


class WorldModelService:
    def __init__(
        self,
        *,
        channel_repository,
        message_repository,
        task_repository,
        trace_repository=None,
    ):
        self.channel_repository = channel_repository
        self.message_repository = message_repository
        self.task_repository = task_repository
        self.trace_repository = trace_repository

    def record_text_observation(self, observation) -> None:
        if observation.guild_id:
            self.channel_repository.upsert_guild(observation.guild_id, observation.guild_name)
        self.channel_repository.upsert_channel(
            observation.channel_id,
            observation.guild_id,
            observation.channel_name,
        )
        self.channel_repository.upsert_user(
            observation.author_id,
            observation.username,
            observation.display_name,
        )
        self.message_repository.save_message(observation)
        if self.trace_repository:
            self.trace_repository.record_trace(
                'world_model_updated',
                'Recorded text observation in world model',
                {'channel_id': observation.channel_id, 'author_id': observation.author_id},
            )

    def get_channel(self, channel_id: str) -> dict | None:
        return self.channel_repository.get_channel(channel_id)

    def get_user(self, user_id: str) -> dict | None:
        return self.channel_repository.get_user(user_id)

    def create_task(self, account_name: str, task_type: str, **kwargs) -> int:
        return self.task_repository.create_task(account_name, task_type, **kwargs)

    def has_pending_task(self, account_name: str, task_type: str, **kwargs) -> bool:
        return self.task_repository.has_pending(account_name, task_type, **kwargs)

    def record_scheduled_task(
        self,
        *,
        account_name: str,
        channel_id: str,
        task_id: int,
        task_type: str,
        run_at: float,
        payload: dict | None = None,
    ) -> None:
        """Trace a queued task."""
        if self.trace_repository:
            self.trace_repository.record_trace('scheduled_task', f'Queued {task_type}', {
                'task_id': task_id,
                'task_type': task_type,
                'run_at': run_at,
                'channel_id': channel_id,
            })

    def list_due_tasks(self, account_name: str, *, now_ts: float | None = None, limit: int = 10) -> list[dict]:
        return self.task_repository.list_due_tasks(account_name, now_ts=now_ts, limit=limit)

    def list_tasks(self, account_name: str, *, status: str | None = None, limit: int = 20) -> list[dict]:
        return self.task_repository.list_tasks(account_name, status=status, limit=limit)

    def record_presence_update(
        self,
        account_name: str,
        *,
        status: str,
        activity: str = '',
        reason: str = '',
        mode: str = '',
    ) -> None:
        if self.trace_repository:
            self.trace_repository.record_trace(
                'presence_updated',
                f'Presence updated: {status} — {activity or "(cleared)"}',
                {
                    'account_name': account_name,
                    'status': status,
                    'activity': activity,
                    'reason': reason,
                    'mode': mode,
                },
            )
