"""Build world-state snapshots for intention generation."""

from __future__ import annotations


class WorldStateBuilder:
    def __init__(
        self,
        *,
        attention_service=None,
        profile_service=None,
        world_model_service=None,
    ):
        self.attention_service = attention_service
        self.profile_service = profile_service
        self.world_model_service = world_model_service

    def from_observation(self, observation) -> dict:
        account_name = observation.account_name
        channel_id = observation.channel_id
        activation = 0.0
        if self.attention_service:
            activation = self.attention_service.channel_activation(account_name, channel_id)
        relationship = {}
        affect = {}
        if self.profile_service:
            context = self.profile_service.build_context(account_name, observation.author_id)
            relationship = context.get('relationship') or {}
            affect = self.profile_service.get_affect(account_name).to_dict()
        return {
            'account_name': account_name,
            'guild_id': observation.guild_id,
            'guild_name': observation.guild_name,
            'channel_id': channel_id,
            'channel_name': observation.channel_name,
            'message_id': observation.message_id,
            'author_id': observation.author_id,
            'username': observation.username,
            'mentioned': bool(observation.mentioned),
            'name_matched': bool(getattr(observation, 'name_matched', False)),
            'respond_trigger': bool(observation.mentioned) or bool(getattr(observation, 'name_matched', False)),
            'activation': activation,
            'relationship': relationship,
            'affect': affect,
            'is_dm': bool(observation.is_dm),
        }

    def from_task(self, task: dict, *, account_name: str) -> dict:
        return {
            'account_name': account_name,
            'channel_id': task.get('target_id') or '',
            'message_id': '',
            'mentioned': False,
            'activation': float(task.get('urgency') or 0.5),
            'task': task,
        }
