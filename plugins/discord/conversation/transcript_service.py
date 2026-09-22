"""Format stored channel messages into LLM-facing transcript lines."""

from __future__ import annotations

DEFAULT_LINE_MAX_CHARS = 1000


def format_message_line(row: dict, *, line_max_chars: int = DEFAULT_LINE_MAX_CHARS) -> str:
    author = (
        str(row.get('author_name') or row.get('display_name') or row.get('username') or 'Unknown').strip()
        or 'Unknown'
    )
    text = str(row.get('content') or row.get('clean_content') or '').replace('\n', ' ').strip()
    line = f'{author}: {text}' if text else f'{author}:'
    if len(line) > line_max_chars:
        line = line[: line_max_chars - 1].rstrip() + '…'
    return line


def format_recent_history(rows: list[dict], *, exclude_message_id: str = '',
                          line_max_chars: int = DEFAULT_LINE_MAX_CHARS) -> list[str]:
    lines: list[str] = []
    for row in rows or []:
        if exclude_message_id and str(row.get('message_id') or '') == exclude_message_id:
            continue
        lines.append(format_message_line(row, line_max_chars=line_max_chars))
    return lines
