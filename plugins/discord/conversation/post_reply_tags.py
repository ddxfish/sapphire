"""Deliver GIF and reaction tags extracted from parsed LLM output."""

from __future__ import annotations


def deliver_gif_and_reaction(
    *,
    runtime,
    parsed,
    message_id: str,
    channel_id: str,
    account_name: str,
    settings,
    trigger_message_id: str = '',
) -> None:
    if not runtime:
        return

    reply_style = getattr(runtime, 'reply_style_service', None)
    gif_service = getattr(runtime, 'gif_service', None)
    transport = getattr(runtime, 'transport', None)
    trace_repository = getattr(runtime, 'trace_repository', None)

    if not transport or not reply_style:
        return

    message_id = str(message_id or '')
    reaction = parsed.reaction
    reaction_on = bool(getattr(getattr(settings, 'reaction', None), 'enabled', True))
    if reaction and trigger_message_id and reaction_on:
        transport.add_reaction_sync(
            channel_id,
            trigger_message_id,
            reaction,
            account_name=account_name or None,
        )

    gif_query = getattr(parsed, 'gif_query', '') or ''
    if not gif_query or reply_style.gif_already_sent(message_id):
        return
    if gif_service:
        gif_query = gif_service.maybe_send_gif(
            parsed,
            account_name=account_name,
            channel_id=channel_id,
            settings=settings,
        )
    if not gif_query or not gif_service:
        return

    url = gif_service.search_gif_url(gif_query, settings=settings)
    if url:
        transport.send_gif_sync(channel_id, url, account_name=account_name or None)
        reply_style.mark_gif_sent(message_id)
        return

    if trace_repository:
        trace_repository.record_trace('gif_skipped', 'GIF search returned no URL', {
            'message_id': message_id,
            'query': gif_query,
        })
