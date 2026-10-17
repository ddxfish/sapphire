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

    if str(prepared.get('proactive_kind') or '').strip():
        additions.append(
            'IMPORTANT: This is a scheduled proactive post, not a reply to a live message. '
            'Write one new message for the channel following the instructions below. '
            'Do NOT set reply_to_message_id — there is no message to quote.'
        )

    reply_instructions = _reply_instruction_text(prepared)
    if reply_instructions:
        additions.append(f'Reply instructions: {reply_instructions}')

    if additions:
        prepared['recent_history'] = history + additions

    return prepared
