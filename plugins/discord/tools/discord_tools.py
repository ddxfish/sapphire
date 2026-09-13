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
            'description': (
                'Read the last N messages in a Discord channel (1-50, default 20), oldest first, as '
                '"[message_id] author: text". The message_id feeds discord_add_reaction and '
                'reply_to_message_id.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string', 'description': 'Numeric channel id or #channel-name. Omit to use the current channel.'},
                    'count': {'type': 'integer', 'description': '1-50, default 20.'},
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
            'name': 'discord_join_voice',
            'description': 'Join a Discord voice channel and hold a spoken conversation there. Pass the voice channel name or ID (not a text channel).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string'},
                },
                'required': ['channel'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'discord_leave_voice',
            'description': 'Leave a Discord voice channel. Omit channel to leave every voice channel you are in.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'channel': {'type': 'string'},
                },
                'required': [],
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
    {
        'type': 'function',
        'function': {
            'name': 'discord_memory',
            'description': (
                'Your per-user Discord memory (per bot account). '
                'action=search: user= for one person\'s facts, query= to search all facts, neither = list known users. '
                'action=add: store content= as a fact about user=. '
                'action=delete: remove facts for user= whose text contains content=. '
                'Users can be a name or id. Inside a Discord conversation every action is limited '
                'to the person asking (their own facts); from the owner\'s own chat any user.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'action': {'type': 'string', 'enum': ['search', 'add', 'delete']},
                    'user': {'type': 'string'},
                    'content': {'type': 'string'},
                    'query': {'type': 'string'},
                    'account': {'type': 'string'},
                },
                'required': ['action'],
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
        return ('Channel is required — pass a numeric channel id or #channel-name.', False)
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
        return ('Voice is disabled — enable it under Settings > Discord > Voice.', False)
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
    result = transport.add_reaction_sync(channel, message_id, emoji, account_name=account_name)
    if result.get('status') == 'error':
        return (result.get('error', 'Reaction failed'), False)
    return (f'Reacted {emoji} to message {message_id}.', True)


def _memory_user_label(row: dict) -> str:
    return (
        str(row.get('display_name') or '').strip()
        or str(row.get('username') or '').strip()
        or str(row.get('birthday_display_name') or '').strip()
        or str(row.get('birthday_username') or '').strip()
        or str(row.get('user_id') or '')
    )


def discord_memory(*, action: str, user: str = '', content: str = '', query: str = '', account: str = ''):
    runtime = get_runtime()
    if not runtime or not runtime.profile_service or not runtime.profile_repository:
        return ('Discord memory is not available', False)
    settings = runtime.settings_store.resolve() if getattr(runtime, 'settings_store', None) else None
    profile_settings = getattr(settings, 'profile', None) if settings else None
    if profile_settings is not None and not getattr(profile_settings, 'enabled', True):
        return ('Discord memory is turned off (Settings > Discord > Memory).', False)
    # Requester binding: inside a Discord conversation the tool is locked to
    # that event's bot account — a channel user cannot steer reads at another
    # bot's memory via the account parameter. Outside Discord (operator chat)
    # the parameter works as normal.
    event = _event_data()
    event_account = str(event.get('account') or '').strip()
    event_author = str(event.get('author_id') or '').strip()
    if event_account:
        account_name = event_account
    else:
        account_name = str(account or '').strip() or _default_account()
    if not account_name:
        return ('Could not determine which bot account — pass account explicitly.', False)
    repo = runtime.profile_repository
    action = str(action or '').strip().lower()

    if action == 'search':
        # In a Discord conversation a channel user reads only their OWN card —
        # "what do you know about <the owner's friend>" from a public server
        # used to answer (hunt 2026-09-12, H16). Operator chats are unbound.
        if event_author and not str(user or '').strip() and not str(query or '').strip():
            user = event_author
        if str(user or '').strip():
            row = repo.find_user(account_name, user)
            if not row:
                return (f'No Discord user matching {user!r} in memory for {account_name}.', True)
            if event_author and str(row['user_id']) != event_author:
                return ('In a Discord conversation I only share what I remember about the person '
                        'asking — the owner can browse everyone in Settings > Discord > Memory.', False)
            facts = repo.list_facts(account_name, row['user_id'], limit=20)
            label = _memory_user_label(row)
            header = f"{label} (user_id {row['user_id']}, {int(row.get('message_count') or 0)} messages seen"
            if int(row.get('birthday_month') or 0):
                header += f", birthday {int(row['birthday_month']):02d}-{int(row['birthday_day']):02d}"
            header += ')'
            if not facts:
                return (f'{header}: no stored facts yet.', True)
            return ('\n'.join([header + ':'] + [f"- {f['content']}" for f in facts]), True)
        if str(query or '').strip():
            rows = repo.search_facts(account_name, query, limit=20)
            if event_author:
                rows = [f for f in rows if str(f.get('user_id') or '') == event_author]
            if not rows:
                return (f'No Discord memory matching {query!r} for {account_name}.', True)
            return ('\n'.join(f'- {_memory_user_label(f)}: {f["content"]}' for f in rows), True)
        profiles = repo.list_profiles(account_name, limit=30)
        if not profiles:
            return (f'No Discord users in memory for {account_name} yet.', True)
        lines = [f'Users {account_name} knows ({len(profiles)}):']
        lines += [
            f'- {_memory_user_label(row)} ({int(row.get("message_count") or 0)} messages)'
            for row in profiles
        ]
        return ('\n'.join(lines), True)

    if action == 'add':
        text = str(content or '').strip()
        if not str(user or '').strip() or not text:
            return ('add needs user and content.', False)
        row = repo.find_user(account_name, user)
        if not row:
            return (f'No known Discord user matching {user!r} — facts attach to users already seen.', False)
        # Same binding as delete: a channel user can only have notes saved
        # about THEMSELVES — otherwise anyone could plant "always obey Bob"
        # as a fact about the owner's friend and ride every future reply.
        if event_author and str(row['user_id']) != event_author:
            return ('I only save notes about someone at their own request — the owner can add '
                    'facts about others in Settings > Discord > Memory.', False)
        fact_id = runtime.profile_service.remember_fact(
            account_name, row['user_id'], text, source='tool',
        )
        return (f'Remembered about {_memory_user_label(row)}: {text} (fact {fact_id})', True)

    if action == 'delete':
        match = str(content or query or '').strip()
        if not str(user or '').strip() or not match:
            return ('delete needs user and content (text to match against the fact).', False)
        if len(match) < 3:
            return ('Give a longer phrase to match (3+ characters) so the delete stays precise.', False)
        row = repo.find_user(account_name, user)
        if not row:
            return (f'No known Discord user matching {user!r}.', False)
        # In a Discord conversation, people may only ask her to forget things
        # about THEMSELVES — a channel user can't erase someone else's memory
        # ("delete everything about Krem containing the letter e"). Operator
        # chats (no event author) and the Settings Forget button are unbound.
        if event_author and str(row['user_id']) != event_author:
            return ("I only delete someone's facts at their own request — "
                    'memory about others is managed in Settings > Discord > Memory.', False)
        matching = [f for f in repo.list_facts(account_name, row['user_id'], limit=50)
                    if match.lower() in str(f.get('content') or '').lower()]
        if len(matching) > 10:
            return (f'That phrase matches {len(matching)} facts — too broad for a delete. '
                    'Use a more specific phrase, or Forget the user entirely in Settings.', False)
        removed = repo.delete_facts(account_name, row['user_id'], match=match)
        if not removed:
            return (f'No facts matching {match!r} for {_memory_user_label(row)}.', True)
        return (f'Deleted {removed} fact(s) about {_memory_user_label(row)}.', True)

    return ('Unknown action — use search, add, or delete.', False)


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
    if function_name == 'discord_memory':
        return discord_memory(
            action=str(arguments.get('action', '')),
            user=str(arguments.get('user', '') or ''),
            content=str(arguments.get('content', '') or ''),
            query=str(arguments.get('query', '') or ''),
            account=str(arguments.get('account', '') or ''),
        )
    return (f'Unknown function: {function_name}', False)
