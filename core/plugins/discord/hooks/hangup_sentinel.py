"""post_chat hook — the <<HANG UP>> sentinel for Discord voice channels.

Sapphire leaves a voice channel by writing <<HANG UP>> in her reply (the voice
prompt block tells her, always — see sapphire/voice_prompt.py). Detection is on
the RAW reply text: core's TTS cleaner strips angle-bracket tags before the
chunk layer, which is also what keeps the tag silent to the channel. This hook
only ARMS the runner; the runner leaves once her final words have drained
(discord_conversation_runner._note_reply_end), after the goodbye chime.

Works regardless of toolset — the VC chat has none by design (C1). Gated to
this reply's own chat via the hook runner's chat_name stamp, so with N voice
sessions the sentinel only ever ends the one it was said in.
"""

from __future__ import annotations

import logging

from plugins.discord.sapphire.voice_chat import is_voice_chat_name
from plugins.discord.sapphire.voice_prompt import HANGUP_MARKER_RE

logger = logging.getLogger(__name__)


def _runtime():
    from plugins.discord.daemon import get_runtime
    return get_runtime()


def post_chat(event) -> None:
    text = getattr(event, 'response', None)
    if not text or '<<' not in text:
        return
    chat_name = getattr(event, 'chat_name', None)
    if chat_name is None:
        logger.warning('[DISCORD] hang-up hook: event carries no chat_name — skipping')
        return
    if not is_voice_chat_name(chat_name) or not HANGUP_MARKER_RE.search(text):
        return
    runtime = _runtime()
    runner = getattr(runtime, 'discord_conversation_runner', None) if runtime else None
    if runner is None:
        logger.warning('[DISCORD] hang-up hook: <<HANG UP>> in %s but no conversation runner', chat_name)
        return
    if runner.arm_leave(str(chat_name)):
        logger.info('[DISCORD] <<HANG UP>> sentinel seen in %s — leaving after the reply drains', chat_name)
    else:
        logger.info('[DISCORD] <<HANG UP>> sentinel in %s but no live voice session for it', chat_name)
