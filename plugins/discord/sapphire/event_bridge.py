import json

from plugins.discord.sapphire.continuity_payload import prepare_continuity_payload


class SapphireEventBridge:
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader
        self._pending_payloads = {}

    def emit(self, event_name: str, payload: str) -> bool:
        if hasattr(self.plugin_loader, 'emit_daemon_event'):
            return bool(self.plugin_loader.emit_daemon_event(event_name, payload))
        return False

    def emit_discord_message(self, payload: dict) -> bool:
        prepared = prepare_continuity_payload(payload)
        accepted = self.emit('discord_message', json.dumps(prepared))
        if accepted:
            self._pending_payloads[str(payload.get('message_id', ''))] = dict(payload)
        return accepted

    def get_pending_payload(self, message_id: str):
        return self._pending_payloads.get(str(message_id))

    def clear_pending_payload(self, message_id: str):
        return self._pending_payloads.pop(str(message_id), None)
