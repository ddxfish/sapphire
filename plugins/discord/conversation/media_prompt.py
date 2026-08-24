"""Format stored media interpretations for the reply LLM prompt."""

from __future__ import annotations


def _media_kind_label(media_kind: str) -> str:
    if media_kind == 'gif':
        return 'GIF'
    if media_kind == 'image':
        return 'image'
    return 'media'


def format_media_context_for_reply(media_items: list[dict]) -> str:
    blocks: list[str] = []
    for item in media_items or []:
        media_kind = str(item.get('media_kind') or 'attachment')
        interpretation = item.get('interpretation') or {}
        summary = str(interpretation.get('summary') or '').strip()
        ocr_text = str(interpretation.get('ocr_text') or '').strip()
        source = str(interpretation.get('source') or '')
        if not summary:
            continue

        kind_label = _media_kind_label(media_kind)
        if source == 'vision':
            blocks.append(
                f'[Attached {kind_label} — automated vision description of what the user sent: '
                f'{summary}]\n'
                f'[Vision pipeline note: you did not receive the raw {kind_label} file. The '
                f'description above is what it shows — including for GIFs. Respond to '
                f'that content directly; do not say you cannot see {kind_label.lower()}s.]'
            )
        else:
            blocks.append(f'Attached {kind_label}: {summary}')
        if ocr_text:
            blocks.append(f'Detected text in {kind_label.lower()}: "{ocr_text}"')

    if not blocks:
        return ''
    return '\n'.join(blocks) + '\n'


def build_reply_content(user_text: str, media_context: list[dict]) -> str:
    content = user_text or ''
    media_block = format_media_context_for_reply(media_context)
    has_user_text = bool(content.strip())
    has_vision = bool(media_block and 'automated vision description of what the user sent' in media_block)
    has_media = bool(media_context)

    if media_block:
        content = media_block + (content if has_user_text else '')

    if has_media and not has_user_text:
        if has_vision:
            kind = _media_kind_label(
                str((media_context[0] or {}).get('media_kind') or 'image')
            )
            content += (
                f'\n[The user sent this {kind} with no caption. Respond to the vision '
                f'description above — that is what the {kind} shows.]\n'
            )
        else:
            content += (
                '\n[The user sent an image/GIF with no text. Use the attachment metadata '
                'above if helpful; otherwise ask them to describe it.]\n'
            )
    elif has_media and has_user_text and has_vision:
        content += (
            '\n[The user also attached media; the vision description above is what '
            'it shows — treat that as having seen their GIF/image.]\n'
        )
    return content
