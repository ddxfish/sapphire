"""The voice gate — a Realtime daemon rule per bot (S6 2026-09-22; filters the same evening).

`Discord: Voice channel` is a realtime source (the twilio incoming_call
pattern): its task is an on/off switch, never fired. The rule names the bot
account; its daemon FILTER (server / channel name or id, `_not` to exclude)
says which voice channels it covers — no filter = every voice channel that
bot can see. Core picks the rule most-specific-wins (get_enabled_daemon_task,
the twilio door): a matching filter beats the catch-all, a failing filter
excludes the rule, no rule = voice is off for that channel. The rule's own
chat config (provider, model, toolset, prompt) configures the VC chat;
`auto_join` (off by default) lets her walk in when someone is there.
"""
from __future__ import annotations

SOURCE = 'discord_voice'
VOICE_OFF_TEXT = 'Voice is off for this channel — no enabled "Discord: Voice channel" Realtime rule covers it.'
_ON = ('1', 'true', 'yes', 'on')


def _flag(tc: dict, key: str) -> bool:
    return str(tc.get(key, '')).strip().lower() in _ON


class VoiceGate:
    def __init__(self, plugin_loader=None, describe=None):
        # describe(account, channel_id) -> {'guild_id', 'guild_name', 'channel_id', 'channel_name'} | None:
        # the transport's guild-cache lookup that fills the filter payload.
        self.plugin_loader = plugin_loader
        self.describe = describe

    def tasks(self, account_name: str) -> list[dict]:
        """Every enabled voice rule for this bot (is voice on at all?)."""
        loader = self.plugin_loader
        fn = getattr(loader, 'tasks_for_source', None) if loader else None
        if not callable(fn):
            return []
        try:
            tasks = fn(SOURCE) or []
        except Exception:
            return []
        account = str(account_name or '')
        return [t for t in tasks if str((t.get('trigger_config') or {}).get('account') or '') == account]

    def payload(self, account_name: str, channel_id: str) -> dict:
        """What a rule's filter sees for one voice channel."""
        info = {}
        if callable(self.describe):
            try:
                info = self.describe(account_name, channel_id) or {}
            except Exception:
                info = {}
        return {'channel_id': str(channel_id or ''), 'guild_id': str(info.get('guild_id') or ''),
                'guild_name': str(info.get('guild_name') or ''), 'channel_name': str(info.get('channel_name') or '')}

    def select(self, account_name: str, payload: dict) -> dict | None:
        """The rule covering this payload (most-specific wins), or None = voice off there."""
        loader = self.plugin_loader
        fn = getattr(loader, 'get_enabled_daemon_task', None) if loader else None
        if not callable(fn):
            return None
        try:
            return fn(SOURCE, account=str(account_name or ''), payload=dict(payload or {}))
        except Exception:
            return None

    def allowed(self, account_name: str, channel_id: str) -> dict | None:
        return self.select(account_name, self.payload(account_name, channel_id))

    @staticmethod
    def auto_join(task: dict | None) -> bool:
        return _flag((task or {}).get('trigger_config') or {}, 'auto_join')

    @staticmethod
    def config(task: dict | None) -> dict:
        """What the VC chat inherits from the rule."""
        task = task or {}
        tc = task.get('trigger_config') or {}
        provider = str(task.get('provider') or '').strip()
        return {
            'task_name': str(task.get('name') or ''),
            'keep_history': _flag(tc, 'keep_chat_history'),
            'llm_provider': '' if provider.lower() == 'auto' else provider,
            'llm_model': str(task.get('model') or '').strip(),
            'toolset': str(task.get('toolset') or '').strip(),
            'prompt': str(task.get('prompt') or '').strip(),
        }
