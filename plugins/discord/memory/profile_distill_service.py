"""Profile distillation via optional Sapphire LLM bridge."""

from __future__ import annotations


class ProfileDistillService:
    def __init__(self, *, profile_repository, profile_service, llm_bridge=None):
        self.profile_repository = profile_repository
        self.profile_service = profile_service
        self.llm_bridge = llm_bridge

    def run_pending(self, account_name: str, user_id: str) -> dict:
        buffers = self.profile_repository.list_pending_buffers(account_name, user_id, limit=20)
        if not buffers:
            return {'status': 'empty'}
        if not self.llm_bridge or not hasattr(self.llm_bridge, 'distill_profile'):
            return {'status': 'skipped', 'reason': 'llm_unavailable'}
        messages = [row['content'] for row in buffers]
        try:
            result = self.llm_bridge.distill_profile(messages)
        except Exception as exc:
            return {'status': 'skipped', 'reason': str(exc)}
        self.profile_service.apply_distillation(account_name, user_id, result)
        self.profile_repository.mark_buffers_processed([row['id'] for row in buffers])
        return {'status': 'distilled', 'buffer_count': len(buffers)}
