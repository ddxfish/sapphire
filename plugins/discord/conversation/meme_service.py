"""Meme classification and send-meme intentions."""

from __future__ import annotations

import re

from plugins.discord.models.intentions import SendMemeIntention
from plugins.discord.models.media import MemeClassification

MEME_PATTERNS = (
    r'meme',
    r'drake',
    r'distracted',
    r'expanding',
    r'wojak',
    r'pepe',
    r'spit.?take',
)


class MemeService:
    def __init__(self, *, meme_library: dict[str, list[str]] | None = None):
        self.meme_library = meme_library or {
            'reaction': ['https://media.tenor.com/example-reaction.gif'],
            'humorous': ['https://media.tenor.com/example-humor.gif'],
        }

    def classify(self, attachments: list[dict]) -> MemeClassification:
        for item in attachments or []:
            haystack = f"{item.get('filename', '')} {item.get('url', '')}".lower()
            if any(re.search(pattern, haystack) for pattern in MEME_PATTERNS):
                sentiment = 'humorous' if 'drake' in haystack or 'distracted' in haystack else 'reaction'
                return MemeClassification(is_meme=True, sentiment=sentiment, role='reaction')
        return MemeClassification()

    def build_intention(self, account_name: str, channel_id: str, *, theme: str = 'reaction') -> SendMemeIntention:
        options = self.meme_library.get(theme) or self.meme_library.get('reaction') or ['']
        meme_url = options[0]
        return SendMemeIntention(
            intention_type='send_meme',
            account_name=account_name,
            channel_id=channel_id,
            message_id='',
            reason='meme_response',
            meme_url=meme_url,
            theme=theme,
            confidence=0.7,
        )

    def evaluate_policy(self, settings, relationship: dict | None = None) -> dict:
        if not settings.media.meme_enabled:
            return {'allowed': False, 'reason': 'meme_disabled'}
        relationship = relationship or {}
        if float(relationship.get('fondness', 0.5)) < 0.2:
            return {'allowed': False, 'reason': 'low_fondness'}
        if float(relationship.get('irritability', 0.0)) > 0.8:
            return {'allowed': False, 'reason': 'high_irritability'}
        return {'allowed': True, 'reason': 'allowed'}
