"""Built-in conservative goals for intention generation."""

from __future__ import annotations


class GoalEngine:
    BUILTIN_GOALS = (
        {'name': 'respond_when_mentioned', 'priority': 0.9},
        {'name': 'maintain_relationships', 'priority': 0.5},
        {'name': 'avoid_intrusion', 'priority': 0.8},
        {'name': 'morning_greeting', 'priority': 0.7},
        {'name': 'quiet_outreach', 'priority': 0.4},
    )

    def active_goals(self, world_state: dict) -> list[dict]:
        goals = []
        for goal in self.BUILTIN_GOALS:
            if goal['name'] == 'respond_when_mentioned' and world_state.get('respond_trigger'):
                goals.append(goal)
            elif goal['name'] == 'maintain_relationships' and world_state.get('activation', 0) >= 0.4:
                goals.append(goal)
            elif goal['name'] == 'avoid_intrusion' and not world_state.get('respond_trigger'):
                goals.append(goal)
        return goals
