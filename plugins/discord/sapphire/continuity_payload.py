def _is_truthy(value) -> bool:
    return str(value).lower() in {'true', '1'}


def _reply_instruction_text(payload: dict) -> str:
    reply_instructions = payload.get('reply_instructions') or ''
    if not reply_instructions and payload.get('reply_hints'):
        hints = payload.get('reply_hints')
        if isinstance(hints, list):
            reply_instructions = '\n\n'.join(str(hint) for hint in hints if hint)
    return str(reply_instructions)


def prepare_continuity_payload(payload: dict) -> dict:
    prepared = dict(payload)
    history = list(prepared.get('recent_history') or [])
    additions = []

    if _is_truthy(prepared.get('plugin_scheduled')):
        additions.append(
            'IMPORTANT: The Discord plugin has already scheduled the follow-up/reminder '
            'described below in its database. Briefly confirm that in your reply. '
            'Do NOT say you cannot set reminders, timers, or scheduled messages.'
        )

    if _is_truthy(prepared.get('task_follow_up')):
        additions.append(
            'IMPORTANT: This is a scheduled reminder follow-up, not a reply to a live message. '
            'Send a new message in the channel and @mention the user. '
            'Do NOT set reply_to_message_id — there is no real message to quote.'
        )

    reply_instructions = _reply_instruction_text(prepared)
    if reply_instructions:
        additions.append(f'Reply instructions: {reply_instructions}')

    if additions:
        prepared['recent_history'] = history + additions

    return prepared
