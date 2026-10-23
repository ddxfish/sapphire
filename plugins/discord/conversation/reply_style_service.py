from __future__ import annotations

from dataclasses import dataclass
import re


from plugins.discord.conversation.gif_service import strip_placeholder_gif_urls
from plugins.discord.conversation.think_tags import strip_think_tags

_FENCE_LINE_RE = re.compile(r'(?m)^[ \t]*```')


@dataclass
class ParsedReply:
    chunks: list[str]
    reaction: str = ''
    gif_query: str = ''
    edit_text: str = ''


class ReplyStyleService:
    def __init__(self, *, message_limit: int = 1900):
        self.message_limit = message_limit
        self._tool_sent: dict[str, str] = {}
        self._gif_sent: set[str] = set()

    def parse_llm_output(self, text: str, *, strip_thinking: bool = True) -> ParsedReply:
        if strip_thinking:
            text = strip_think_tags(text)
        reaction = ''
        gif_query = ''
        edit_text = ''
        edit_tags = re.findall(r'\[edit:([^\]]{1,500})\]', text or '', flags=re.IGNORECASE)
        if edit_tags:
            edit_text = edit_tags[-1].strip()
        text = re.sub(r'\[edit:[^\]]{1,500}\]', '', text or '', flags=re.IGNORECASE)
        gif_tags = re.findall(r'\[gif:([^\]]{1,120})\]', text or '', flags=re.IGNORECASE)
        if gif_tags:
            gif_query = gif_tags[0].strip()
        text = re.sub(r'\[gif:[^\]]{1,120}\]', '', text or '', flags=re.IGNORECASE)
        react_tags = re.findall(r'\[react:([^\]]{1,64})\]', text or '', flags=re.IGNORECASE)
        if react_tags:
            reaction = react_tags[0].strip()
        text = re.sub(r'\[react:[^\]]{1,64}\]', '', text or '', flags=re.IGNORECASE)
        lines = []
        for raw in (text or '').splitlines():
            stripped = raw.strip()
            if re.fullmatch(r'\[break\]', stripped, flags=re.IGNORECASE):
                lines.append('[break]')
                continue
            lines.append(raw)
        content = strip_placeholder_gif_urls("\n".join(lines).strip())
        chunks = self._split_message(content)
        return ParsedReply(chunks=chunks, reaction=reaction, gif_query=gif_query, edit_text=edit_text)

    def _split_message(self, text: str) -> list[str]:
        if not text:
            return []
        parts = re.split(r'\[break\]', text, flags=re.IGNORECASE)
        chunks: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            chunks.extend(self._split_discord_length(part))
        return self._rebalance_fences(chunks)

    @staticmethod
    def _rebalance_fences(chunks: list[str]) -> list[str]:
        """A ``` block cut by a chunk boundary rendered as raw backticks in
        one message and swallowed the next. Close it at the cut, reopen after."""
        out: list[str] = []
        carry = False
        for chunk in chunks:
            text = ('```\n' + chunk) if carry else chunk
            # Only a fence that STARTS a line opens/closes a block — a bare ```
            # inside prose ("you type ``` before it") used to flip every later
            # chunk into a code block (hunt 2.13.0, row 34).
            if len(_FENCE_LINE_RE.findall(text)) % 2 == 1:
                text = text + '\n```'
                carry = True
            else:
                carry = False
            out.append(text)
        return out

    def _split_discord_length(self, text: str) -> list[str]:
        if not text:
            return []
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
        chunks: list[str] = []
        for paragraph in paragraphs:
            remaining = paragraph
            while len(remaining) > self.message_limit:
                split_at = remaining.rfind(' ', 0, self.message_limit)
                if split_at <= 0:
                    split_at = self.message_limit
                chunks.append(remaining[:split_at].strip())
                remaining = remaining[split_at:].strip()
            if remaining:
                chunks.append(remaining)
        return chunks

    def mark_tool_sent(self, message_id: str, text: str = '') -> None:
        self._tool_sent[str(message_id)] = text

    def mark_gif_sent(self, message_id: str) -> None:
        self._gif_sent.add(str(message_id))

    def gif_already_sent(self, message_id: str) -> bool:
        return str(message_id) in self._gif_sent

    def should_skip_auto_reply(self, message_id: str) -> bool:
        return str(message_id) in self._tool_sent

    def discard(self, message_id: str) -> None:
        """Forget a message's latches without delivering — the listen-only lane
        never reached the consumers, so both grew forever (row 24)."""
        self._tool_sent.pop(str(message_id), None)
        self._gif_sent.discard(str(message_id))

    def consume_tool_sent_text(self, message_id: str) -> str:
        return self._tool_sent.pop(str(message_id), '')
