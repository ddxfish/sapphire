"""Track conversational interest topics for quiet outreach hints."""

from __future__ import annotations

import re

# Curated topic lexicon: canonical topic -> keyword aliases.
# Lightweight and deterministic — no LLM, no Sapphire core memory.
TOPIC_LEXICON: dict[str, tuple[str, ...]] = {
    'gaming': ('gaming', 'gamer', 'videogame', 'video game', 'steam', 'xbox', 'playstation', 'nintendo', 'esports'),
    'music': ('music', 'song', 'album', 'playlist', 'concert', 'band', 'spotify', 'guitar', 'piano'),
    'coding': ('coding', 'programming', 'software', 'python', 'javascript', 'typescript', 'github', 'deploy', 'bugfix', 'refactor'),
    'anime': ('anime', 'manga', 'weeb', 'otaku'),
    'movies': ('movie', 'movies', 'film', 'cinema', 'netflix'),
    'tv': ('tv show', 'series', 'binge', 'episode'),
    'sports': ('sports', 'football', 'soccer', 'basketball', 'baseball', 'hockey', 'nba', 'nfl', 'mlb'),
    'cooking': ('cooking', 'recipe', 'baking', 'kitchen', 'foodie'),
    'food': ('food', 'restaurant', 'pizza', 'sushi', 'brunch', 'coffee'),
    'travel': ('travel', 'vacation', 'trip', 'flight', 'airport', 'hotel'),
    'art': ('art', 'drawing', 'painting', 'illustration', 'sketch'),
    'photography': ('photography', 'photo', 'camera', 'lens'),
    'books': ('book', 'books', 'reading', 'novel', 'audiobook'),
    'science': ('science', 'physics', 'chemistry', 'biology', 'astronomy'),
    'tech': ('tech', 'gadget', 'smartphone', 'hardware', 'ai', 'llm'),
    'pets': ('pet', 'pets', 'dog', 'cat', 'puppy', 'kitten'),
    'fitness': ('fitness', 'gym', 'workout', 'running', 'yoga'),
    'gardening': ('garden', 'gardening', 'plants', 'houseplant'),
    'streaming': ('stream', 'streaming', 'twitch', 'youtube'),
    'discord': ('discord', 'server', 'moderation', 'nitro'),
}

_HASHTAG_RE = re.compile(r'#([a-zA-Z][\w-]{1,30})')
# Word-bounded alias patterns. `alias in raw` matched SUBSTRINGS — 'ai' in
# said/again, 'art' in party, 'cat' in location, 'pet' in competition, 'band'
# in husband, 'book' in facebook — and fed every message into the interest
# graph (hunt 2026-09-12, H8).
_ALIAS_PATTERNS: dict[str, re.Pattern] = {
    topic: re.compile(r'\b(?:' + '|'.join(re.escape(alias) for alias in aliases) + r')\b')
    for topic, aliases in TOPIC_LEXICON.items()
}


def extract_topics(text: str, *, max_topics: int = 5) -> list[str]:
    """Extract canonical interest topics from a message. Deterministic."""
    raw = str(text or '').strip().lower()
    if not raw:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def _add(topic: str) -> None:
        topic = topic.strip().lower()
        if not topic or topic in seen:
            return
        seen.add(topic)
        found.append(topic)

    # A hashtag counts only when it names a lexicon topic: on Discord a bare
    # #word in clean_content is almost always a channel mention (#general).
    for tag in _HASHTAG_RE.findall(raw):
        canonical = _canonical_from_token(tag)
        if canonical:
            _add(canonical)

    for topic, pattern in _ALIAS_PATTERNS.items():
        if pattern.search(raw):
            _add(topic)
        if len(found) >= max_topics:
            break

    return found[:max_topics]


def _canonical_from_token(token: str) -> str | None:
    token = token.lower().replace('-', ' ').replace('_', ' ')
    for topic, aliases in TOPIC_LEXICON.items():
        if token == topic or token in aliases:
            return topic
    return None


class InterestService:
    def __init__(self, *, interest_repository):
        self.interest_repository = interest_repository

    def observe_message(self, account_name: str, user_id: str, text: str) -> list[dict]:
        topics = extract_topics(text)
        return [
            self.interest_repository.bump(account_name, user_id, topic)
            for topic in topics
        ]

    def top_topics(self, account_name: str, user_id: str, *, limit: int = 8) -> list[dict]:
        return self.interest_repository.list_for_user(account_name, user_id, limit=limit)

    def channel_topics(
        self,
        account_name: str,
        user_ids: list[str],
        *,
        limit: int = 6,
    ) -> list[dict]:
        return self.interest_repository.top_for_users(account_name, user_ids, limit=limit)

    def outreach_hint(self, topics: list[dict]) -> str:
        names = [str(row.get('topic') or '').strip() for row in topics if row.get('topic')]
        names = [n for n in names if n][:4]
        if not names:
            return ''
        if len(names) == 1:
            return f'People here have talked about {names[0]} lately — you could gently pick that up.'
        joined = ', '.join(names[:-1]) + f', or {names[-1]}'
        return f'People here have talked about {joined} lately — you could gently pick one of those up.'

    def seed_topic(self, account_name: str, user_id: str, topic: str, *, weight: float = 2.0) -> dict:
        """Operator test helper."""
        return self.interest_repository.bump(account_name, user_id, topic, weight_delta=weight)
