import json
import time

from plugins.discord.sapphire.continuity_payload import prepare_continuity_payload

# A payload is kept until its reply lands; a reply that never comes (LLM error,
# listen-only task) used to keep it forever (M9). Older than this = swept.
PENDING_PAYLOAD_TTL_SECONDS = 1800.0


class SapphireEventBridge:
    def __init__(self, plugin_loader, *, llm_debug_service=None):
        self.plugin_loader = plugin_loader
        self.llm_debug_service = llm_debug_service
        self._pending_payloads = {}

    def emit(self, event_name: str, payload: str) -> bool:
        if hasattr(self.plugin_loader, 'emit_daemon_event'):
            return bool(self.plugin_loader.emit_daemon_event(event_name, payload))
        return False

    def emit_discord_message(self, payload: dict) -> bool:
        debug_extra = payload.pop('_debug_prompt_context', None)
        if self.llm_debug_service:
            self.llm_debug_service.record_prompt(payload, extra=debug_extra or {})
        prepared = prepare_continuity_payload(payload)
        data = json.dumps(prepared)
        # Chat-only tasks and All-interactions tasks both hear every message
        # (S1, 2026-09-22); a bot with one of each answers twice by design.
        accepted = self.emit('discord_message', data)
        accepted = self.emit('discord_all', data) or accepted
        if accepted:
            self._sweep_pending()
            self._pending_payloads[str(payload.get('message_id', ''))] = (dict(payload), time.time())
        return accepted

    def _sweep_pending(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        stale = [mid for mid, (_p, at) in self._pending_payloads.items() if now - at > PENDING_PAYLOAD_TTL_SECONDS]
        for mid in stale:
            self._pending_payloads.pop(mid, None)
        return len(stale)

    def clear_pending_payload(self, message_id: str):
        entry = self._pending_payloads.pop(str(message_id), None)
        return entry[0] if entry else None
