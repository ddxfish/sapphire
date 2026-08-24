"""Structured trace recording for operator observability."""

from __future__ import annotations


class TraceService:
    def __init__(self, *, trace_repository):
        self.trace_repository = trace_repository

    def record(self, trace_type: str, summary: str, detail: dict | None = None) -> None:
        self.trace_repository.record_trace(trace_type, summary, detail or {})

    def record_intention(self, intention_type: str, detail: dict) -> None:
        self.record('intention_generated', f'Generated {intention_type} intention', {
            'intention_type': intention_type,
            **detail,
        })

    def record_policy_rejection(self, reason: str, detail: dict) -> None:
        self.record('policy_rejected', f'Policy blocked action: {reason}', {
            'reason': reason,
            **detail,
        })

    def record_memory_injection(self, detail: dict) -> None:
        self.record('memory_injected', 'Memory context added to prompt', detail)

    def record_affect_modulation(self, detail: dict) -> None:
        self.record('affect_modulated', 'Affect state adjusted', detail)

    def record_proactive_action(self, action: str, detail: dict) -> None:
        self.record('proactive_action', f'Proactive action: {action}', {
            'action': action,
            **detail,
        })

    def record_media_interpreted(self, detail: dict) -> None:
        self.record('media_interpreted', 'Media artifact interpreted', detail)

    def record_voice_decision(self, decision: str, detail: dict) -> None:
        self.record('voice_decision', f'Voice decision: {decision}', {
            'decision': decision,
            **detail,
        })

    def list_recent(self, *, limit: int = 50, trace_type: str | None = None) -> list[dict]:
        return self.trace_repository.list_traces(limit=limit, trace_type=trace_type)

    def summary(self, *, limit: int = 100) -> dict:
        traces = self.list_recent(limit=limit)
        counts: dict[str, int] = {}
        for item in traces:
            counts[item['trace_type']] = counts.get(item['trace_type'], 0) + 1
        return {'total': len(traces), 'by_type': counts}
