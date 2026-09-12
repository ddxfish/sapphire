"""Format stored channel messages into LLM-facing transcript lines."""

from __future__ import annotations

DEFAULT_LINE_MAX_CHARS = 1000
_MEDIA_SUMMARY_MAX_CHARS = 240


def _media_kind_label(media_kind: str) -> str:
    if media_kind == 'gif':
        return 'GIF'
    if media_kind == 'image':
        return 'image'
    return 'media'


def _compact_summary(text: str, *, max_chars: int = _MEDIA_SUMMARY_MAX_CHARS) -> str:
    cleaned = ' '.join((text or '').split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + '…'


def _media_note(media_items: list[dict]) -> str:
    notes: list[str] = []
    for item in media_items or []:
        media_kind = str(item.get('media_kind') or 'media')
        interpretation = item.get('interpretation') or {}
        summary = str(interpretation.get('summary') or '').strip()
        if not summary:
            filename = str(item.get('filename') or '').strip()
            summary = filename or f'{_media_kind_label(media_kind).lower()} attachment'
        label = _media_kind_label(media_kind)
        notes.append(f'{label}: {_compact_summary(summary)}')
    return '; '.join(notes)


def format_message_line(
    row: dict,
    *,
    media_items: list[dict] | None = None,
    line_max_chars: int = DEFAULT_LINE_MAX_CHARS,
) -> str:
    author = (
        str(row.get('author_name') or row.get('display_name') or row.get('username') or 'Unknown').strip()
        or 'Unknown'
    )
    text = str(row.get('content') or row.get('clean_content') or '').replace('\n', ' ').strip()
    media_note = _media_note(media_items or [])
    if media_note:
        if text:
            text = f'{text} [{media_note}]'
        else:
            text = f'[sent {media_note}]'
    line = f'{author}: {text}' if text else f'{author}:'
    if len(line) > line_max_chars:
        line = line[: line_max_chars - 1].rstrip() + '…'
    return line


def format_recent_history(
    rows: list[dict],
    media_by_message: dict[str, list[dict]] | None = None,
    *,
    exclude_message_id: str = '',
    line_max_chars: int = DEFAULT_LINE_MAX_CHARS,
) -> list[str]:
    media_by_message = media_by_message or {}
    lines: list[str] = []
    for row in rows or []:
        message_id = str(row.get('message_id') or '')
        if exclude_message_id and message_id == exclude_message_id:
            continue
        lines.append(
            format_message_line(
                row,
                media_items=media_by_message.get(message_id, []),
                line_max_chars=line_max_chars,
            )
        )
    return lines
