"""Doors for add-on plugins (S0, 2026-09-21).

Six free-form hooks the host FIRES through core's hook_runner (precedent:
twilio_call_ended). Any plugin registers a handler by name in its manifest —
``"hooks": {"discord_message_observed": "hooks.py"}`` — and never imports
transport: every event carries ``metadata['api']``, the DiscordAPI facade.

    discord_message_observed  every inbound message, before trigger eval  read-only
    discord_prompt_context    building a triggered reply's payload        append metadata['context_parts']
    discord_reply_planned     reply parsed into chunks, before send       chunks · quote_reply · reaction · delay_s
    discord_reply_sent        after send                                  read-only
    discord_voice_utterance   post-STT text in a voice channel            read-only
    discord_tick              runtime loop, per connected account (~15 s) read-only

Threads: observed + tick handlers run on a worker thread (their fire sites
sit on the daemon's asyncio loop); prompt_context runs ON the loop and is
synchronous because its result is used inline — keep handlers cheap there.
reply_planned / reply_sent / voice_utterance already run on worker threads.
The facade is loop-aware: a write called from the loop is queued and answers
{'status': 'queued'}; a read from the loop answers empty (nothing to await).

Privacy stamp: Discord traffic is not a Sapphire chat. Events are stamped
chat_private=False so the runner never resolves the web UI's active chat for
them (twilio pattern). The runner logs and skips a raising handler.
"""
from __future__ import annotations

import asyncio
import logging

from core.hooks import HookEvent, hook_runner

logger = logging.getLogger(__name__)

HOOKS = (
    'discord_message_observed', 'discord_prompt_context', 'discord_reply_planned',
    'discord_reply_sent', 'discord_voice_utterance', 'discord_tick',
)
_announced: set = set()


def _runtime():
    from plugins.discord.daemon import get_runtime
    return get_runtime()


def _summary(payload: dict) -> str:
    keys = ('account', 'channel_id', 'message_id', 'speaker_name', 'proactive_kind')
    bits = [f'{k}={payload[k]}' for k in keys if payload.get(k) not in (None, '')]
    if 'chunks' in payload:
        bits.append(f'chunks={len(payload.get("chunks") or [])}')
    if 'text' in payload:
        bits.append(f'text={len(str(payload.get("text") or ""))}ch')
    return ' '.join(bits)


def fire(name: str, payload: dict) -> HookEvent:
    """Fire one host hook synchronously; returns the (possibly mutated) event."""
    ev = HookEvent()
    ev.chat_name = None
    ev.chat_private = False
    ev.metadata = {'hook': name, 'api': api(), **(payload or {})}
    live = hook_runner.has_handlers(name)
    if live and name not in _announced:
        # One "door is open" line per hook per process — watchable at INFO
        # without a log line per message.
        _announced.add(name)
        logger.info('[DISCORD] hook %s live: %d handler(s)', name, len(hook_runner.get_handlers(name)))
    logger.debug('[DISCORD] hook %s (%s)%s', name, _summary(payload or {}), '' if live else ' — no handlers')
    if not live:
        return ev
    try:
        hook_runner.fire(name, ev)
    except Exception:
        logger.exception('[DISCORD] hook %s failed', name)
    return ev


def fire_threaded(name: str, payload: dict) -> None:
    """Fire off the daemon loop when called on one (observational hooks only)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        fire(name, payload)
        return
    loop.run_in_executor(None, fire, name, payload)


def observed_payload(obs) -> dict:
    """The read-only view of an inbound message every hook shares."""
    return {
        'account': obs.account_name,
        'guild_id': str(obs.guild_id or ''),
        'guild_name': obs.guild_name,
        'channel_id': str(obs.channel_id or ''),
        'channel_name': obs.channel_name,
        'message_id': str(getattr(obs, 'message_id', '') or ''),
        'author_id': str(obs.author_id or ''),
        'username': obs.username,
        'display_name': obs.display_name,
        'content': getattr(obs, 'clean_content', '') or getattr(obs, 'content', '') or '',
        'is_dm': bool(obs.is_dm),
        'is_bot': bool(getattr(obs, 'author_is_bot', False)),
        'mentioned': bool(getattr(obs, 'mentioned', False)),
        'timestamp': obs.created_at,
        'attachments': len(getattr(obs, 'attachments', []) or []),
    }


_IMAGE_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}


class DiscordAPI:
    """What add-ons may do to Discord. Thin and loop-aware; every call answers
    a dict ({'status': ...}) or a list and never raises into the handler."""

    # -- plumbing --------------------------------------------------------
    def _transport(self):
        rt = _runtime()
        return getattr(rt, 'transport', None) if rt else None

    @staticmethod
    def _on_loop(transport) -> bool:
        try:
            return asyncio.get_running_loop() is getattr(transport, 'loop', None)
        except RuntimeError:
            return False

    def _write(self, sync_call, async_call):
        t = self._transport()
        if t is None:
            return {'status': 'error', 'error': 'discord runtime unavailable'}
        try:
            if self._on_loop(t):
                asyncio.ensure_future(async_call(t))
                return {'status': 'queued'}
            return sync_call(t)
        except Exception as exc:
            logger.warning('[DISCORD] api write failed: %s', exc, exc_info=True)
            return {'status': 'error', 'error': str(exc)}

    def _read(self, sync_call, empty):
        t = self._transport()
        if t is None:
            return empty
        if self._on_loop(t):
            logger.warning('[DISCORD] api read from the daemon loop — call it from a worker thread (tick/observed handlers are)')
            return empty
        try:
            return sync_call(t)
        except Exception as exc:
            logger.warning('[DISCORD] api read failed: %s', exc, exc_info=True)
            return empty

    # -- reads -------------------------------------------------------------
    def accounts(self) -> list:
        t = self._transport()
        try:
            return list(t.list_connected()) if t else []
        except Exception:
            return []

    def recent_messages(self, account, channel_id, limit: int = 20) -> list:
        return self._read(lambda t: t.read_messages(str(channel_id), count=int(limit or 20),
                                                    account_name=account), [])

    def channel_info(self, channel_id, account=None) -> dict:
        return self._read(lambda t: t.channel_reach_sync(str(channel_id), account_name=account), {})

    # -- writes ------------------------------------------------------------
    def send_message(self, channel_id, text, reply_to=None, account=None) -> dict:
        cid = str(channel_id)
        return self._write(
            lambda t: t.send_message_sync(cid, text, reply_to_message_id=reply_to, account_name=account),
            lambda t: t.send_message_async(cid, text, reply_to_message_id=reply_to, account_name=account))

    def edit_message(self, channel_id, message_id, text, account=None) -> dict:
        """Edit one of the bot's own messages (a typo fix, an afterthought)."""
        return self._write(
            lambda tr: tr.edit_message_sync(str(channel_id), str(message_id), str(text), account_name=account),
            lambda tr: tr._execution.edit_message(str(channel_id), str(message_id), str(text), account_name=account),
        )

    def react(self, channel_id, message_id, emoji, account=None) -> dict:
        cid, mid = str(channel_id), str(message_id)
        return self._write(
            lambda t: t.add_reaction_sync(cid, mid, emoji, account_name=account),
            lambda t: t.add_reaction_async(cid, mid, emoji, account_name=account))

    def set_presence(self, account, status: str = 'online', activity: str = '') -> dict:
        return self._write(
            lambda t: t.change_presence_sync(account, status=status, activity=activity),
            lambda t: t.change_presence_async(account, status=status, activity=activity))

    def send_image(self, channel_id, source: str, caption: str = '', account=None) -> dict:
        """Post an image by core.images handle — img:<id>, doc:<N> or a URL.
        Never a filesystem path (the tools' rule, H3)."""
        source = str(source or '').strip()
        if not source.startswith(('img:', 'doc:', 'http://', 'https://')):
            return {'status': 'error', 'error': 'source must be img:<id>, doc:<N> or a URL'}
        from core import images as ci
        try:
            resolved = ci.resolve(source)
        except Exception as exc:
            return {'status': 'error', 'error': str(exc)}
        media_type = str(resolved.media_type or '')
        data = resolved.data if media_type in _IMAGE_TYPES else ci.for_chat(resolved.data)
        ext = _IMAGE_TYPES.get(media_type, 'jpg')
        cid, name = str(channel_id), f'image.{ext}'
        return self._write(
            lambda t: t.send_file_sync(cid, data, name, caption=caption or '', account_name=account),
            lambda t: t.execution.send_file(cid, data, name, caption=caption or '', account_name=account))

    # -- voice -------------------------------------------------------------
    def _voice(self):
        rt = _runtime()
        return getattr(rt, 'voice_service', None) if rt else None

    def join_voice(self, account, channel_id, guild_id: str = '') -> dict:
        vs = self._voice()
        if vs is None:
            return {'status': 'error', 'error': 'voice service unavailable'}
        from plugins.discord.models.intentions import JoinVoiceIntention
        it = JoinVoiceIntention(intention_type='join_voice', account_name=account, channel_id=str(channel_id),
                                message_id='', reason='api', guild_id=str(guild_id or ''))
        try:
            if self._on_loop(self._transport()):
                asyncio.ensure_future(vs.join_async(it))
                return {'status': 'queued'}
            return vs.join(it)
        except Exception as exc:
            return {'status': 'error', 'error': str(exc)}

    def leave_voice(self, account, channel_id) -> dict:
        vs = self._voice()
        if vs is None:
            return {'status': 'error', 'error': 'voice service unavailable'}
        from plugins.discord.models.intentions import LeaveVoiceIntention
        it = LeaveVoiceIntention(intention_type='leave_voice', account_name=account, channel_id=str(channel_id),
                                 message_id='', reason='api')
        try:
            if self._on_loop(self._transport()):
                asyncio.ensure_future(vs.leave_async(it))
                return {'status': 'queued'}
            return vs.leave(it)
        except Exception as exc:
            return {'status': 'error', 'error': str(exc)}


_API = DiscordAPI()


def api() -> DiscordAPI:
    return _API
