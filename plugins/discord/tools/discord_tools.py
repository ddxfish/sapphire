from __future__ import annotations

import logging
import re
from contextvars import ContextVar

from plugins.discord.daemon import get_runtime, run_coroutine
from plugins.discord.voice.voice_gate import VOICE_OFF_TEXT

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎮'

_reply_channel_id = ContextVar('discord_reply_channel_id', default=None)
_reply_message_id = ContextVar('discord_reply_message_id', default=None)
_reply_account = ContextVar('discord_reply_account', default=None)


def _valid_reply_message_id(message_id) -> str | None:
    """Return a Discord snowflake suitable for quote-replies, or None."""
    mid = str(message_id or '').strip()
    if not mid or mid.startswith('task-followup-'):
        return None
    if mid.isdigit():
        return mid
    return None


# Every Discord tool talks to Discord's API: is_local='endpoint' (refused in
# private chats), network=True (UI badge). Channel params take an id or a
# #name; discord_list_channels is the way to learn either (D11, AIX report
# 2026-09-13: she ran get_servers and had no way to find a channel id).
TOOLS = [
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_list_channels',
            'description': (
                'List channels the bot can see, one per line as "#name (id) — server". '
                'Use this to find a channel name or id before posting or reading; a channel '
                'from the list can be passed to any other Discord tool as its id or #name.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'server': {'type': 'string', 'description': 'Server name or id to filter by (substring match). Omit = every server.'},
                    'kind': {'type': 'string', 'enum': ['text', 'voice', 'all'], 'description': 'text (default), voice, or all.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_get_servers',
            'description': 'List the servers the bot is in as "name (id)". For channels use discord_list_channels.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_read_messages',
            'description': (
                'Read the last N messages in a Discord channel (1-50, default 20), oldest first, as '
                '"[message_id] author: text". The message_id feeds discord_add_reaction and '
                'reply_to_message_id.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit = the channel you are replying in.'},
                    'count': {'type': 'integer', 'description': '1-50, default 20.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_send_message',
            'description': (
                'Send a Discord message (long text is split at 1900 chars). Returns the message id. '
                'Inside a conversation, omit channel to reply where you were spoken to.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit = the channel you are replying in.'},
                    'text': {'type': 'string', 'description': 'The message. Plain text or Discord markdown.'},
                    'reply_to_message_id': {'type': 'string', 'description': 'Quote-reply to this message id (from discord_read_messages).'},
                },
                'required': ['text'],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_send_image',
            'description': (
                'Post an image to a Discord channel. source = img:<id> (the "(image img:...)" handle a tool '
                'gave you), doc:<N> (a library image from your memory), or a URL; omit it to send the newest '
                'image of this chat. You see the image too and can describe it.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit = the channel you are replying in.'},
                    'source': {'type': 'string', 'description': 'img:<id>, doc:<N>, or https://... (default: newest image of this chat).'},
                    'caption': {'type': 'string', 'description': 'Text posted with the image.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_send_gif',
            'description': 'Send a GIF by search query or by URL. Returns the message id. Needs a GIF API key in Media settings for searches.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'A search query ("happy dance") or a direct GIF URL.'},
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit = the channel you are replying in.'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_join_voice',
            'description': 'Join a Discord voice channel and hold a spoken conversation there. Pass the VOICE channel name or id (see discord_list_channels kind=voice).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Voice channel name or numeric id.'},
                },
                'required': ['channel'],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_leave_voice',
            'description': 'Leave a Discord voice channel. Omit channel to leave every voice channel you are in.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Voice channel name or numeric id. Omit = all.'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'is_local': 'endpoint',
        'network': True,
        'function': {
            'name': 'discord_add_reaction',
            'description': 'Add an emoji reaction to a message. Inside a conversation, omit message_id to react to the message you are replying to.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'emoji': {'type': 'string', 'description': 'A unicode emoji, e.g. 🔥.'},
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit = the channel you are replying in.'},
                    'message_id': {'type': 'string', 'description': 'Numeric message id (from discord_read_messages). Omit = the message being replied to.'},
                },
                'required': ['emoji'],
            },
        },
    },
]

AVAILABLE_FUNCTIONS = {item['function']['name'] for item in TOOLS}


def _transport():
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return None
    return runtime.transport


def _event_data() -> dict:
    try:
        from core.continuity.executor import current_event_data

        event = current_event_data.get() or {}
        return event if isinstance(event, dict) else {}
    except ImportError:
        return {}


def _known_account_names() -> set[str]:
    runtime = get_runtime()
    if not runtime or not runtime.transport:
        return set()
    return set(runtime.transport._accounts.keys())


def _reply_channel_fallback() -> str:
    if _reply_channel_id.get():
        return str(_reply_channel_id.get()).strip()
    event = _event_data()
    return str(event.get('channel_id') or '').strip()


def _resolve_channel_id(channel_arg: str) -> str:
    """Map tool channel args to a Discord snowflake channel id."""
    channel_arg = str(channel_arg or '').strip().lstrip('#')
    if not channel_arg:
        return _reply_channel_fallback()
    if channel_arg.isdigit():
        return channel_arg
    if channel_arg in _known_account_names():
        return _reply_channel_fallback()
    event = _event_data()
    if channel_arg == str(event.get('account') or '').strip():
        return _reply_channel_fallback()
    runtime = get_runtime()
    if runtime and runtime.transport:
        try:
            resolved = runtime.transport.resolve_channel_id_sync(
                channel_arg,
                account_name=_default_account(),
            )
            if resolved:
                return str(resolved)
        except Exception as exc:
            logger.debug('Channel name resolution failed for %r: %s', channel_arg, exc)
    return channel_arg


def _tools_stay_in_server() -> bool:
    runtime = get_runtime()
    store = getattr(runtime, 'settings_store', None) if runtime else None
    if store is None:
        return True
    try:
        return bool(getattr(store.resolve().safety, 'tools_stay_in_server', True))
    except Exception:
        return True


def _reach_error(channel: str, account_name: str | None) -> str | None:
    """H3 (hunt 2026-09-12): inside a server event her tools may only act in
    THAT server — a message in one server must not make her post into another
    or into someone's DMs. Operator chats (no event) reach everything. Fail
    closed: an unresolvable target inside an event is refused."""
    event = _event_data()
    if not event or not str(event.get('channel_id') or ''):
        return None                                   # operator chat — full reach
    if not _tools_stay_in_server():
        return None
    target = str(channel or '').strip()
    event_channel = str(event.get('channel_id') or '')
    if target == event_channel:
        return None
    if str(event.get('is_dm', '')).lower() in {'1', 'true'}:
        return "From inside a DM I only act in this DM. Post there from the owner's chat instead."
    transport = _transport()
    reach_fn = getattr(transport, 'channel_reach_sync', None)
    if not callable(reach_fn):
        return 'I cannot verify which server that channel is in from here, so I will not post there.'
    try:
        reach = reach_fn(target, account_name=account_name) or {}
    except Exception as exc:
        return f'I cannot resolve channel {target} from here ({exc}), so I will not post there.'
    if reach.get('is_dm'):
        return 'From inside a server I never post into DMs.'
    event_guild = str(event.get('guild_id') or '')
    if not event_guild:
        # Fail CLOSED: an event with no guild id used to skip the cross-server
        # check entirely (hunt 2.13.0, row 9). The event channel itself stays
        # reachable (handled above).
        return "I can't tell which server this conversation is in, so I only act in this channel."
    if str(reach.get('guild_id') or '') != event_guild:
        return ("That channel is in a different server than this conversation. From inside a server "
                "I only act in this server — ask the owner to post there from their own chat.")
    return None


class AmbiguousBot(RuntimeError):
    """Two or more bots are connected and nothing says which one she is — refuse
    rather than post as whichever bot sorts first (Krem, 2026-09-22)."""


def _scope_account() -> str | None:
    try:
        from core.chat.function_manager import scope_discord

        acct = scope_discord.get()
        if acct and acct not in ('none', 'default', ''):
            return str(acct).strip()
    except (ImportError, AttributeError):
        return None
    return None


def _default_account() -> str | None:
    if _reply_account.get():
        return str(_reply_account.get()).strip()
    acct = _scope_account()
    if acct:
        return acct
    event = _event_data()
    account = str(event.get('account') or '').strip()
    if account:
        return account
    runtime = get_runtime()
    if runtime and runtime.transport:
        connected = runtime.transport.list_connected()
        if len(connected) == 1:
            return connected[0]
        if len(connected) > 1:
            raise AmbiguousBot(f"{len(connected)} Discord bots are connected ({', '.join(connected)}) and this chat has no "
                               "Discord bot scope, so I don't know which bot to act as. Set the chat's Discord scope "
                               "to the bot you want and try again.")
    return None


def _default_channel(arguments: dict) -> str:
    raw = arguments.get('channel') or arguments.get('channel_id') or ''
    if raw:
        return _resolve_channel_id(str(raw))
    return _resolve_channel_id('')


def _correlation_message_id() -> str:
    """Message id used to correlate tool sends with auto-reply deduplication."""
    mid = _valid_reply_message_id(_reply_message_id.get())
    if mid:
        return mid
    event = _event_data()
    return str(event.get('message_id') or '').strip()


def _mark_gif_sent() -> None:
    runtime = get_runtime()
    message_id = _correlation_message_id()
    if runtime and runtime.reply_style_service and message_id:
        runtime.reply_style_service.mark_gif_sent(message_id)


def _mark_tool_sent(text: str = '') -> None:
    runtime = get_runtime()
    message_id = _correlation_message_id()
    if runtime and runtime.reply_style_service and message_id:
        runtime.reply_style_service.mark_tool_sent(message_id, text)


def discord_get_servers():
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    servers = transport.list_servers()
    event = _event_data()
    if event and _tools_stay_in_server():
        # Its sibling discord_list_channels was filtered; this one disclosed
        # every server from inside any of them (row 33).
        event_guild = str(event.get('guild_id') or '')
        servers = [s for s in servers if str(s.get('id') or '') == event_guild]
    if not servers:
        return ('No servers available (bot may still be connecting).', True)
    return ('\n'.join(f"{item['name']} ({item['id']})" for item in servers), True)


def discord_list_channels(*, server: str = '', kind: str = 'text'):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    kind = str(kind or 'text').strip().lower()
    kinds = ['text', 'voice'] if kind == 'all' else [kind if kind in ('text', 'voice') else 'text']
    rows: list[dict] = []
    for k in kinds:
        try:
            for row in transport.list_channels_sync(k) or []:
                rows.append({**row, 'kind': k})
        except Exception as exc:
            return (f'Could not list {k} channels: {exc}', False)
    needle = str(server or '').strip().lower()
    if needle:
        rows = [r for r in rows if needle in str(r.get('guild_name') or '').lower() or needle == str(r.get('guild_id') or '')]
    # Inside a server event, the roster is that server's (H3 reach).
    event = _event_data()
    event_guild = str(event.get('guild_id') or '') if event else ''
    if event and _tools_stay_in_server():
        # inside ANY event: this server only; no guild id = nothing else (row 9)
        rows = [r for r in rows if str(r.get('guild_id') or '') == event_guild]
    if not rows:
        return ('No channels found' + (f' matching {server!r}' if needle else '') + ' (bot may still be connecting).', True)
    lines = []
    for r in rows:
        tag = ' [voice]' if r.get('kind') == 'voice' else ''
        lines.append(f"#{r.get('channel_name')} ({r.get('channel_id')}) — {r.get('guild_name')} ({r.get('guild_id')}){tag}")
    return ('\n'.join(lines), True)


def discord_send_image(*, channel=None, source: str = '', caption: str = ''):
    """Post an image from core.images (img:/doc:/URL) — the Telegram plugin's
    lane, verbatim. Nothing here reads a filesystem path (H3: the old upload
    tool took any absolute path)."""
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    account_name = _default_account()
    reach = _reach_error(channel, account_name)
    if reach:
        return (reach, False)
    from core import images as ci

    source = str(source or '').strip()
    if source and not (source.startswith(('img:', 'doc:', 'http://', 'https://'))):
        return ('source must be img:<id>, doc:<N>, or a URL — I do not post files from disk.', False)
    if not source:
        last = ci.last_image_id()
        if not last:
            return ('No image to send — give source= (img:<id>, doc:<N>, or a URL) or make one first.', False)
        source = f'img:{last}'
    try:
        resolved = ci.resolve(source)
    except ci.ImageError as exc:
        return (str(exc), False)
    media_type = str(resolved.media_type or '')
    if media_type in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        data = resolved.data
    else:
        data = ci.for_chat(resolved.data)
        media_type = 'image/jpeg'
    ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}.get(media_type, 'jpg')
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(resolved.label or 'image').split('\n', 1)[0])[:40].strip('_') or 'image'
    result = transport.send_file_sync(channel, data, f'{stem}.{ext}', caption=str(caption or ''), account_name=account_name)
    if result.get('status') == 'error':
        return (result.get('error', 'Image send failed'), False)
    receipt = f" (message_id {result['message_id']})" if result.get('message_id') else ''
    text = f'Image {resolved.label.splitlines()[0] if resolved.label else source} sent to channel {channel}{receipt}.'
    return (ci.result(text, [ci.for_chat(data)]), True)


def discord_read_messages(channel=None, count=20):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required — pass a numeric channel id or #channel-name.', False)
    reach = _reach_error(channel, _default_account())
    if reach:
        return (reach, False)
    try:
        rows = transport.read_messages(
            channel, count=max(1, min(50, int(count or 20))), account_name=_default_account(),
        )
    except Exception as exc:
        return (f'Could not read channel {channel}: {exc}', False)
    if not rows:
        return (f'No messages in channel {channel}.', True)
    lines = [f'Last {len(rows)} messages in channel {channel} (oldest first; message_id in brackets):']
    for row in rows:
        stamp = f" {row['created_at']}" if row.get('created_at') else ''
        extra = f" [+{row['attachments']} attachment(s)]" if row.get('attachments') else ''
        who = row.get('author') or row.get('author_id') or '?'
        lines.append(f"[{row.get('message_id')}]{stamp} {who}: {row.get('content') or ''}{extra}")
    return ('\n'.join(lines), True)


def discord_send_message(*, text: str, channel=None, reply_to_message_id=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    event = _event_data()
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    runtime = get_runtime()
    account_name = _default_account()
    reach = _reach_error(channel, account_name)
    if reach:
        return (reach, False)
    reply_style = runtime.reply_style_service if runtime else None
    settings = (
        runtime.settings_store.resolve()
        if runtime and getattr(runtime, 'settings_store', None)
        else None
    )
    parsed = reply_style.parse_llm_output(text) if reply_style else None
    chunks = parsed.chunks if parsed else [text]
    if not chunks and not (parsed and (parsed.gif_query or parsed.reaction)):
        return ('Message text is empty.', False)
    sent_parts = []
    sent_ids: list[str] = []
    reply_to = _valid_reply_message_id(reply_to_message_id or _reply_message_id.get())
    for index, chunk in enumerate(chunks):
        if str(chunk or '').strip():
            result = transport.send_message_sync(
                channel,
                chunk,
                reply_to_message_id=reply_to if index == 0 else None,
                account_name=account_name,
            )
            if result.get('status') == 'error':
                return (result.get('error', 'Send failed'), False)
            sent_parts.append(chunk)
            for row in (result.get('messages') or []):
                if isinstance(row, dict) and row.get('message_id'):
                    sent_ids.append(str(row['message_id']))
    if parsed and not sent_parts and not parsed.gif_query and not parsed.reaction:
        return ('Message text is empty.', False)
    _mark_tool_sent('\n\n'.join(sent_parts))
    conversation = getattr(runtime, 'conversation_service', None) if runtime else None
    if parsed and conversation is not None:
        conversation.deliver_tags(
            parsed, message_id=_correlation_message_id(), channel_id=channel,
            account_name=account_name or '', settings=settings, trigger_message_id=_correlation_message_id(),
        )
    receipt = f' (message_id {", ".join(sent_ids)})' if sent_ids else ''
    return (f'Message sent to channel {channel}{receipt}.', True)


# discord_upload_file was REMOVED 2026-09-13 (hunt H3): it took any absolute
# path with only an exists() check — one persuaded turn could post the owner's
# settings, bot tokens, or signing key into a channel. Not rebuilt by design.


def discord_send_gif(*, query: str, channel=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    runtime = get_runtime()
    account_name = _default_account()
    reach = _reach_error(channel, account_name)
    if reach:
        return (reach, False)
    settings = (
        runtime.settings_store.resolve()
        if runtime and getattr(runtime, 'settings_store', None)
        else None
    )
    message_id = str(_reply_message_id.get() or '')
    if runtime and runtime.reply_style_service and message_id and runtime.reply_style_service.gif_already_sent(message_id):
        return ('GIF already sent for this message.', True)
    if runtime and runtime.gif_service and not str(query).startswith('http'):
        if not runtime.gif_service.gif_allowed(settings):
            return ('GIF replies are disabled or no API key is configured.', False)
        url = runtime.gif_service.search_gif_url(query, settings=settings)
        if not url:
            return (f'No GIF found for query: {query}', False)
        query = url
    result = transport.send_gif_sync(channel, query, account_name=account_name)
    if result.get('status') == 'error':
        return (result.get('error', 'GIF send failed'), False)
    _mark_gif_sent()
    receipt = f" (message_id {result['message_id']})" if result.get('message_id') else ''
    return (f'GIF sent to channel {channel}{receipt}.', True)


def discord_join_voice(*, channel: str):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'voice_service', None):
        return ('Voice runtime is not available', False)
    account_name = _default_account()
    if not account_name:
        return ('Could not determine which bot account to use for voice.', False)
    try:
        resolved = transport.resolve_voice_channel_sync(channel, account_name=account_name)
    except Exception as exc:
        return (f'Voice channel not found: {exc}', False)
    reach = _reach_error(str(resolved.get('channel_id') or ''), account_name)   # H3 (row 33)
    if reach:
        return (reach, False)
    from plugins.discord.models.intentions import JoinVoiceIntention

    intention = JoinVoiceIntention(
        intention_type='join_voice',
        account_name=account_name,
        channel_id=str(resolved.get('channel_id') or ''),
        message_id='',
        reason='tool_request',
        guild_id=str(resolved.get('guild_id') or ''),
    )
    result = runtime.voice_service.join(intention)
    status = result.get('status')
    channel_name = resolved.get('channel_name') or channel
    if status == 'joined':
        return (f"Joined voice channel '{channel_name}'.", True)
    if status == 'blocked':
        return (VOICE_OFF_TEXT, False)
    return (str(result.get('reason') or 'Voice join failed'), False)


def discord_leave_voice(*, channel=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'voice_service', None):
        return ('Voice runtime is not available', False)
    account_name = _default_account()
    if not account_name:
        return ('Could not determine which bot account to use for voice.', False)
    if channel:
        try:
            resolved = transport.resolve_voice_channel_sync(channel, account_name=account_name)
        except Exception as exc:
            return (f'Voice channel not found: {exc}', False)
        targets = [str(resolved.get('channel_id') or '')]
    else:
        voice_transport = getattr(runtime, 'voice_transport', None)
        targets = [
            str(row.get('channel_id') or '')
            for row in (voice_transport.list_connections(account_name) if voice_transport else [])
        ]
    # H3 (row 33): from inside a server, only that server's voice channel(s).
    targets = [t for t in targets if t and not _reach_error(t, account_name)]
    if not targets:
        return ('Not connected to any voice channel.', True)
    from plugins.discord.models.intentions import LeaveVoiceIntention

    for channel_id in targets:
        runtime.voice_service.leave(LeaveVoiceIntention(
            intention_type='leave_voice',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='tool_request',
        ))
    return ('Left voice channel.' if len(targets) == 1 else f'Left {len(targets)} voice channels.', True)


def discord_add_reaction(*, emoji: str, channel=None, message_id=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    message_id = str(message_id or _reply_message_id.get() or '').strip()
    message_id = _valid_reply_message_id(message_id) or ''
    if not channel or not message_id:
        return ('Channel and message_id are required.', False)
    account_name = _default_account()
    reach = _reach_error(channel, account_name)
    if reach:
        return (reach, False)
    result = transport.add_reaction_sync(channel, message_id, emoji, account_name=account_name)
    if result.get('status') == 'error':
        return (result.get('error', 'Reaction failed'), False)
    return (f'Reacted {emoji} to message {message_id}.', True)


def execute(function_name, arguments, config=None):
    arguments = arguments or {}
    try:
        from core.continuity.executor import current_event_data

        event = current_event_data.get() or {}
        if isinstance(event, dict):
            # Set-or-CLEAR inside an event: these used to stick from the previous
            # event on a reused drain thread when the current one lacked the
            # field — a queued proactive post quote-replied a stale id (row 25).
            # No event (operator chat / core-provided routing) leaves them alone.
            if event:
                _reply_channel_id.set(str(event['channel_id']) if event.get('channel_id') else None)
                _reply_message_id.set(_valid_reply_message_id(event.get('message_id')))
            if event.get('account'):
                _reply_account.set(str(event['account']))
            else:
                acct = _scope_account()
                if acct:
                    _reply_account.set(acct)
    except ImportError:
        pass
    try:
        return _dispatch(function_name, arguments)
    except AmbiguousBot as exc:
        return (str(exc), False)


def _dispatch(function_name, arguments):
    if function_name == 'discord_get_servers':
        return discord_get_servers()
    if function_name == 'discord_list_channels':
        return discord_list_channels(
            server=str(arguments.get('server', '') or ''),
            kind=str(arguments.get('kind', 'text') or 'text'),
        )
    if function_name == 'discord_send_image':
        return discord_send_image(
            channel=_default_channel(arguments),
            source=str(arguments.get('source', '') or ''),
            caption=str(arguments.get('caption', '') or ''),
        )
    if function_name == 'discord_read_messages':
        return discord_read_messages(
            channel=_default_channel(arguments),
            count=int(arguments.get('count', 20) or 20),
        )
    if function_name == 'discord_send_message':
        return discord_send_message(
            text=str(arguments.get('text', '')),
            channel=_default_channel(arguments),
            reply_to_message_id=arguments.get('reply_to_message_id'),
        )
    if function_name == 'discord_send_gif':
        return discord_send_gif(
            query=str(arguments.get('query', '')),
            channel=_default_channel(arguments),
        )
    if function_name == 'discord_join_voice':
        # No reply-channel fallback: that would be a TEXT channel id.
        return discord_join_voice(channel=str(arguments.get('channel', '')))
    if function_name == 'discord_leave_voice':
        return discord_leave_voice(channel=str(arguments.get('channel', '') or '') or None)
    if function_name == 'discord_add_reaction':
        return discord_add_reaction(
            emoji=str(arguments.get('emoji', '')),
            channel=_default_channel(arguments),
            message_id=arguments.get('message_id'),
        )
    return (f'Unknown function: {function_name}', False)
