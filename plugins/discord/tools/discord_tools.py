from __future__ import annotations

import logging
from contextvars import ContextVar

from plugins.discord.daemon import get_runtime, run_coroutine

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🎮'

_reply_channel_id = ContextVar('discord_reply_channel_id', default=None)
_reply_message_id = ContextVar('discord_reply_message_id', default=None)
_reply_account = ContextVar('discord_reply_account', default=None)
_task_follow_up_key = ContextVar('discord_task_follow_up_key', default=None)
_task_follow_up_sent = ContextVar('discord_task_follow_up_sent', default=False)


def _valid_reply_message_id(message_id) -> str | None:
    """Return a Discord snowflake suitable for quote-replies, or None."""
    mid = str(message_id or '').strip()
    if not mid or mid.startswith('task-followup-'):
        return None
    if mid.isdigit():
        return mid
    return None


TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'discord_get_servers',
            'description': 'List Discord servers.',
            'parameters': {'type': 'object', 'properties': {}, 'required': []},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_read_messages',
            'description': 'Read recent messages from a Discord channel.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string'},
                    'count': {'type': 'integer'},
                },
                'required': [],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_send_message',
            'description': 'Send a Discord message.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string'},
                    'text': {'type': 'string'},
                    'reply_to_message_id': {'type': 'string'},
                },
                'required': ['text'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_upload_file',
            'description': 'Upload a file to Discord.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'file_path': {'type': 'string'},
                    'channel': {'type': 'string'},
                    'caption': {'type': 'string'},
                },
                'required': ['file_path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_send_gif',
            'description': 'Send a GIF to Discord by search query or URL. Omit channel to reply in the current channel; use channel_id (numeric) or #channel-name — not the bot account name.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'channel': {'type': 'string'},
                },
                'required': ['query'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_add_reaction',
            'description': 'Add a reaction in Discord.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'emoji': {'type': 'string'},
                    'channel': {'type': 'string'},
                    'message_id': {'type': 'string'},
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


def _task_follow_up_event(event: dict | None = None) -> bool:
    event = event if event is not None else _event_data()
    return str(event.get('task_follow_up', '')).lower() in {'1', 'true'}


def _task_id_from_event(event: dict) -> int | None:
    raw = event.get('task_id')
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    message_id = str(event.get('message_id') or '')
    if message_id.startswith('task-followup-'):
        try:
            return int(message_id.rsplit('-', 1)[-1])
        except (TypeError, ValueError):
            return None
    return None


def _bind_task_follow_up_context(event: dict) -> None:
    if not _task_follow_up_event(event):
        return
    key = str(event.get('message_id') or event.get('task_id') or '')
    if _task_follow_up_key.get() != key:
        _task_follow_up_key.set(key)
        _task_follow_up_sent.set(False)


def _task_follow_up_already_delivered(event: dict | None = None) -> bool:
    event = event if event is not None else _event_data()
    if not _task_follow_up_event(event):
        return False
    if _task_follow_up_sent.get() and _task_follow_up_key.get() == str(
        event.get('message_id') or event.get('task_id') or ''
    ):
        return True
    task_id = _task_id_from_event(event)
    if not task_id:
        return False
    runtime = get_runtime()
    if not runtime or not getattr(runtime, 'world_model_service', None):
        return False
    row = runtime.world_model_service.task_repository.get_task(task_id)
    return bool(row and row.get('status') == 'completed')


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
    event = _event_data()
    if _task_follow_up_event(event):
        _task_follow_up_sent.set(True)
        conversation_service = (
            getattr(runtime, 'conversation_service', None) if runtime else None
        )
        if conversation_service:
            conversation_service._complete_task_follow_up_if_needed(event)


def discord_get_servers():
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    servers = transport.list_servers()
    if not servers:
        return ('No servers available (bot may still be connecting).', True)
    return ('\n'.join(f"{item['name']} ({item['id']})" for item in servers), True)


def discord_read_messages(channel=None, count=20):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    return (str(transport.read_messages(channel, count=count)), True)


def discord_send_message(*, text: str, channel=None, reply_to_message_id=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    event = _event_data()
    if _task_follow_up_already_delivered(event):
        return ('Reminder already delivered for this follow-up.', True)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    runtime = get_runtime()
    account_name = _default_account()
    reply_style = runtime.reply_style_service if runtime else None
    settings = (
        runtime.settings_store.resolve(
            guild_id=str(event.get('guild_id') or ''),
            channel_id=channel,
            dm_id=channel if str(event.get('is_dm', '')).lower() in {'1', 'true'} else None,
        )
        if runtime and getattr(runtime, 'settings_store', None)
        else None
    )
    parsed = reply_style.parse_llm_output(text) if reply_style else None
    chunks = parsed.chunks if parsed else [text]
    if not chunks and not (parsed and (parsed.gif_query or parsed.reaction)):
        return ('Message text is empty.', False)
    sent_parts = []
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
    if parsed and not sent_parts and not parsed.gif_query and not parsed.reaction:
        return ('Message text is empty.', False)
    _mark_tool_sent('\n\n'.join(sent_parts))
    if parsed and runtime:
        from plugins.discord.conversation.post_reply_tags import deliver_gif_and_reaction

        deliver_gif_and_reaction(
            runtime=runtime,
            parsed=parsed,
            message_id=_correlation_message_id(),
            channel_id=channel,
            account_name=account_name or '',
            settings=settings,
            trigger_message_id=_correlation_message_id(),
        )
    return (f'Message sent to channel {channel}.', True)


def discord_upload_file(*, file_path: str, channel=None, caption=''):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    try:
        result = transport.upload_file_sync(channel, file_path, caption=caption, account_name=_default_account())
    except FileNotFoundError:
        return (f'File not found: {file_path}', False)
    if result.get('status') == 'error':
        return (result.get('error', 'Upload failed'), False)
    return (f'Uploaded {file_path} to channel {channel}.', True)


def discord_send_gif(*, query: str, channel=None):
    transport = _transport()
    if not transport:
        return ('Discord runtime is not available', False)
    channel = _resolve_channel_id(str(channel or ''))
    if not channel:
        return ('Channel is required.', False)
    runtime = get_runtime()
    account_name = _default_account()
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
    return (f'GIF sent to channel {channel}.', True)


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
            _bind_task_follow_up_context(event)
            if event.get('channel_id'):
                _reply_channel_id.set(str(event['channel_id']))
            valid_message_id = _valid_reply_message_id(event.get('message_id'))
            if valid_message_id:
                _reply_message_id.set(valid_message_id)
            if event.get('account'):
                _reply_account.set(str(event['account']))
            else:
                acct = _scope_account()
                if acct:
                    _reply_account.set(acct)
    except ImportError:
        pass
    if function_name == 'discord_get_servers':
        return discord_get_servers()
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
    if function_name == 'discord_upload_file':
        return discord_upload_file(
            file_path=str(arguments.get('file_path', '')),
            channel=_default_channel(arguments),
            caption=str(arguments.get('caption', '')),
        )
    if function_name == 'discord_send_gif':
        return discord_send_gif(
            query=str(arguments.get('query', '')),
            channel=_default_channel(arguments),
        )
    if function_name == 'discord_add_reaction':
        return discord_add_reaction(
            emoji=str(arguments.get('emoji', '')),
            channel=_default_channel(arguments),
            message_id=arguments.get('message_id'),
        )
    return (f'Unknown function: {function_name}', False)
