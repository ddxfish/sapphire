"""Human imperfection: typos, post-send edits, and quote-reply heuristics."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from typing import Optional

from plugins.discord.conversation.sentiment import pick_reaction_emoji
from plugins.discord.conversation.typo_wordlist import RAW_MISSPELLINGS

logger = logging.getLogger(__name__)

QUOTE_BASE = 0.40
QUOTE_DM_MIN, QUOTE_DM_MAX = 0.10, 0.20
QUOTE_BUSY_BATCH = 0.65
QUOTE_BUSY_HISTORY = 0.55
QUOTE_THREAD_BOOST = 0.55
BUSY_BATCH_THRESHOLD = 5
BUSY_HISTORY_THRESHOLD = 2

EDIT_CHANCE = 0.04
EDIT_DELAY_MIN, EDIT_DELAY_MAX = 2.0, 5.0
THOUGHT_SUFFIXES = (' lol', ' haha', ' anyway', ' — wait nvm', ' actually', ' idk')

_QUESTION_RE = re.compile(
    r'\?\s*$|^\s*(who|what|when|where|why|how|is|are|do|does|did|can|could|would|will|should)\b',
    re.I,
)
_JOKE_MARKERS = ('lol', 'lmao', 'haha', 'hehe', '😂', '🤣', 'jk', 'nah', 'bruh')
_WORD_RE_CACHE: dict[str, re.Pattern] = {}


@dataclass
class DeliveryPlan:
    chunks: list[str]
    reply_to_message_id: str | None = None
    edit_delay: float = 0.0
    edit_chunk_index: int = 0
    edit_text: str = ''


class DeliveryStyleService:
    def plan_delivery(
        self,
        *,
        parsed,
        raw_text: str,
        event_data: dict,
        settings,
        trigger_content: str = '',
    ) -> DeliveryPlan:
        delivery = getattr(settings, 'delivery', None) if settings else None
        chunks = list(parsed.chunks or [])
        reply_to = self._resolve_reply_target(parsed, event_data, delivery, trigger_content)

        if not chunks:
            return DeliveryPlan(chunks=[], reply_to_message_id=reply_to)

        edit_text = str(getattr(parsed, 'edit_text', '') or '').strip()
        first_chunk = chunks[0]

        if edit_text and delivery and delivery.message_edits_enabled:
            explicit = self._plan_explicit_edit(first_chunk, edit_text, raw_text=raw_text, chunks=chunks)
            if explicit:
                delay, send_text, corrected, chunk_index = explicit
                chunks = list(chunks)
                chunks[chunk_index] = send_text
                return DeliveryPlan(
                    chunks=chunks,
                    reply_to_message_id=reply_to,
                    edit_delay=delay,
                    edit_chunk_index=chunk_index,
                    edit_text=corrected,
                )

        if delivery and delivery.auto_typo_enabled and delivery.message_edits_enabled:
            typo_plan = self._plan_auto_typo(first_chunk, delivery, trigger_content)
            if typo_plan:
                delay, typo_text, corrected = typo_plan
                chunks = list(chunks)
                chunks[0] = typo_text
                return DeliveryPlan(
                    chunks=chunks,
                    reply_to_message_id=reply_to,
                    edit_delay=delay,
                    edit_chunk_index=0,
                    edit_text=corrected,
                )

        if delivery and delivery.message_edits_enabled and delivery.post_send_edit_enabled:
            legacy = self._plan_post_send_edit(first_chunk)
            if legacy:
                delay, send_text, corrected = legacy
                chunks = list(chunks)
                chunks[0] = send_text
                return DeliveryPlan(
                    chunks=chunks,
                    reply_to_message_id=reply_to,
                    edit_delay=delay,
                    edit_chunk_index=0,
                    edit_text=corrected,
                )

        return DeliveryPlan(chunks=chunks, reply_to_message_id=reply_to)

    def _resolve_reply_target(self, parsed, event_data: dict, delivery, trigger_content: str) -> str | None:
        if delivery and not delivery.quote_reply_enabled:
            return None
        message_id = str(event_data.get('message_id') or '')
        if message_id.startswith('task-followup-'):
            return None
        reply_text = '\n\n'.join(parsed.chunks or [])
        if not self.should_quote_reply(event_data, trigger_content, reply_text):
            return None
        raw_reply_to = str(event_data.get('reply_to_message_id') or '').strip()
        if raw_reply_to:
            return raw_reply_to
        return message_id or None

    def should_quote_reply(self, event_data: dict, trigger_content: str, reply_text: str) -> bool:
        chance = self.compute_quote_reply_chance(event_data, trigger_content, reply_text)
        if chance >= 1.0:
            return True
        if chance <= 0.0:
            return False
        return random.random() < chance

    def compute_quote_reply_chance(self, event_data: dict, trigger_content: str, reply_text: str) -> float:
        if self._is_question(trigger_content):
            return 1.0
        if self._trigger_has_media(event_data):
            return 0.0
        if self._looks_like_joke_or_comment(trigger_content, reply_text):
            return 0.0

        chance = QUOTE_BASE
        if str(event_data.get('is_dm', '')).lower() in {'true', '1'}:
            return random.uniform(QUOTE_DM_MIN, QUOTE_DM_MAX)
        if int(event_data.get('batch_size') or 1) > BUSY_BATCH_THRESHOLD:
            return QUOTE_BUSY_BATCH
        if len(event_data.get('recent_history') or []) > BUSY_HISTORY_THRESHOLD:
            chance = max(chance, QUOTE_BUSY_HISTORY)
        return min(1.0, max(0.0, chance))

    def _plan_auto_typo(self, text: str, delivery, trigger_content: str) -> Optional[tuple[float, str, str]]:
        if '?' in (trigger_content or ''):
            return None
        chance = max(0.0, min(100.0, float(delivery.auto_typo_chance)))
        if chance <= 0 or random.random() >= (chance / 100.0):
            return None
        pair = self._introduce_common_typo(text)
        if not pair:
            return None
        typo_text, corrected = pair
        delay_min = float(delivery.auto_typo_delay_min)
        delay_max = float(delivery.auto_typo_delay_max)
        if delay_max < delay_min:
            delay_min, delay_max = delay_max, delay_min
        delay = random.uniform(max(0.5, delay_min), max(delay_min, delay_max))
        return delay, typo_text, corrected

    def _plan_explicit_edit(
        self,
        sent_text: str,
        edited_text: str,
        *,
        raw_text: str,
        chunks: list[str],
    ) -> Optional[tuple[float, str, str, int]]:
        sent = (sent_text or '').strip()
        edited = (edited_text or '').strip()
        if not sent or not edited or sent == edited:
            return None
        delay = random.uniform(EDIT_DELAY_MIN, EDIT_DELAY_MAX)
        chunk_index = self._resolve_edit_chunk_index(chunks, raw_text)
        sent_chunk = (chunks[chunk_index] if chunks else sent).strip()
        if '\n' in sent_chunk and edited not in sent_chunk:
            lines = sent_chunk.splitlines()
            if len(lines) > 1:
                fixed = '\n'.join(lines[:-1] + [edited])
                if fixed != sent_chunk:
                    return delay, sent_chunk, fixed, chunk_index
        trailing = self._replace_trailing_typo_phrase(sent_chunk, edited)
        corrected = trailing if trailing and trailing != sent_chunk else edited
        return delay, sent_chunk, corrected, chunk_index

    def _plan_post_send_edit(self, text: str) -> Optional[tuple[float, str, str]]:
        stripped = (text or '').strip()
        if len(stripped) < 8:
            return None
        if random.random() >= EDIT_CHANCE:
            return None
        delay = random.uniform(EDIT_DELAY_MIN, EDIT_DELAY_MAX)
        if random.random() < 0.5:
            typo = self._introduce_subtle_typo(stripped)
            if typo == stripped:
                return None
            return delay, typo, stripped
        suffix = random.choice(THOUGHT_SUFFIXES)
        if stripped.lower().endswith(suffix.strip().lower()):
            return None
        return delay, stripped, stripped + suffix

    def _introduce_common_typo(self, text: str) -> Optional[tuple[str, str]]:
        stripped = (text or '').strip()
        if len(stripped) < 8:
            return None
        candidates = []
        for correct, typos in RAW_MISSPELLINGS.items():
            for match in self._word_pattern(correct).finditer(stripped):
                original = match.group(0)
                typo_form = random.choice(typos)
                candidates.append((
                    match.start(),
                    match.end(),
                    original,
                    self._apply_case(typo_form, original),
                ))
        if not candidates:
            return None
        start, end, _original, typo_word = random.choice(candidates)
        typo_text = stripped[:start] + typo_word + stripped[end:]
        if typo_text == stripped:
            return None
        return typo_text, stripped

    def _introduce_subtle_typo(self, text: str) -> str:
        words = text.split()
        candidates = [index for index, word in enumerate(words) if len(word) >= 5 and any(char.isalpha() for char in word)]
        if not candidates:
            return text
        index = random.choice(candidates)
        word = words[index]
        alpha_positions = [pos for pos, char in enumerate(word) if char.isalpha()]
        if len(alpha_positions) < 2:
            return text
        pos = random.choice(alpha_positions[1:-1] if len(alpha_positions) > 2 else alpha_positions[1:])
        words[index] = word[:pos] + word[pos] + word[pos:]
        return ' '.join(words)

    def _resolve_edit_chunk_index(self, chunks: list[str], raw_text: str) -> int:
        if not chunks:
            return 0
        if len(chunks) == 1:
            return 0
        lower = (raw_text or '').lower()
        pos = lower.rfind('[edit:')
        if pos < 0:
            return len(chunks) - 1
        before = (raw_text or '')[:pos].rstrip()
        for index in range(len(chunks) - 1, -1, -1):
            candidate = chunks[index].strip()
            if candidate and before.endswith(candidate):
                return index
        return len(chunks) - 1

    def _replace_trailing_typo_phrase(self, sent: str, edited: str) -> Optional[str]:
        if not sent or not edited or edited in sent or sent == edited:
            return None
        words_sent = sent.split()
        words_edit = edited.split()
        if not words_sent or not words_edit:
            return None
        for count in range(min(6, len(words_sent)), 0, -1):
            tail = ' '.join(words_sent[-count:])
            if tail == edited:
                return None
            if words_sent[-1].lower() == words_edit[-1].lower():
                prefix = ' '.join(words_sent[:-count])
                return f'{prefix} {edited}'.strip() if prefix else edited
        return None

    def _word_pattern(self, word: str) -> re.Pattern:
        key = word.lower()
        pattern = _WORD_RE_CACHE.get(key)
        if pattern is None:
            pattern = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
            _WORD_RE_CACHE[key] = pattern
        return pattern

    @staticmethod
    def _apply_case(typo: str, original: str) -> str:
        if not original:
            return typo
        if original.isupper():
            return typo.upper()
        if original[0].isupper():
            return typo[0].upper() + typo[1:] if typo else typo
        return typo.lower()

    @staticmethod
    def _is_question(text: str) -> bool:
        if not text:
            return False
        last_line = text.strip().split('\n')[-1].strip()
        return bool(_QUESTION_RE.search(last_line))

    @staticmethod
    def _trigger_has_media(event_data: dict) -> bool:
        return bool(event_data.get('attachments'))

    @staticmethod
    def _looks_like_joke_or_comment(trigger_content: str, reply_text: str) -> bool:
        if DeliveryStyleService._is_question(trigger_content):
            return False
        reply = (reply_text or '').strip()
        if not reply or '?' in reply:
            return False
        lower = reply.lower()
        if any(marker in lower for marker in _JOKE_MARKERS):
            return True
        return len(reply) <= 120 and not DeliveryStyleService._is_question(reply)
