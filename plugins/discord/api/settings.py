"""Settings routes: the daemon's state and built-in defaults. Values live in core
(GET/PUT /api/webui/plugins/discord/settings); per-guild overlays are gone (S6)."""

from __future__ import annotations

from plugins.discord.daemon import get_health_state, is_daemon_alive
from plugins.discord.sapphire.voice_prompt import default_conversation_prompt_template


def get_settings(**kwargs):
    return {
        'defaults': {
            'voice': {
                'conversation_prompt_template': default_conversation_prompt_template(),
            },
        },
        'daemon_running': is_daemon_alive(),
        'daemon_state': get_health_state(),
    }
