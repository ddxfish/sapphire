"""Heuristics for rejecting Whisper output on corrupted Discord/DAVE audio."""

from __future__ import annotations

import re
from collections import Counter

from core.stt.hallucination import is_whisper_hallucination

# Common noise hallucinations on garbled VC audio (not in global filter).
_DISCORD_NOISE_PHRASES = (
    "i'm not sure",
    'im not sure',
    'all right',
    'oh my',
    'thank you for watching',
    'thanks for watching',
)

# Whisper often invents these on sub-2s DAVE-degraded clips.
_SHORT_CLIP_HALLUCINATIONS = (
    "let's do it",
    'lets do it',
    "let's go",
    'lets go',
    "alright let's go",
    'alright lets go',
    'sounds good',
    'ready when you are',
    "i'll see you next time",
    'ill see you next time',
    'see you next time',
    'see you later',
    'see you mind',
    "i'm not",
    'im not',
    'best one',
)


_GARBAGE_TAIL_WORDS = frozenset({
    'oom',
    'mind',
})


def is_garbage_tail_transcript(text: str) -> bool:
    words = [word.strip(".,!?") for word in str(text or '').lower().split()]
    if len(words) < 2:
        return False
    return words[-1] in _GARBAGE_TAIL_WORDS


def is_repetitive_noise_transcript(text: str) -> bool:
    """True when the same short phrase dominates the transcript."""
    words = re.findall(r"[a-z0-9']+", (text or '').lower())
    if len(words) < 6:
        return False
    for phrase_len in (2, 3, 4):
        if len(words) < phrase_len * 3:
            continue
        phrases = [' '.join(words[index:index + phrase_len]) for index in range(len(words) - phrase_len + 1)]
        _phrase, count = Counter(phrases).most_common(1)[0]
        if count >= 3 and (count * phrase_len) >= len(words) * 0.45:
            return True
    return False


def _dominant_noise_phrase(text: str) -> bool:
    lowered = ' '.join((text or '').lower().split())
    if not lowered:
        return True
    for phrase in _DISCORD_NOISE_PHRASES:
        if lowered.count(phrase) >= 3:
            return True
    return False


def is_low_confidence_segments(segment_list, *, peak: float) -> bool:
    if not segment_list:
        return False
    avg_no_speech = sum(float(getattr(s, 'no_speech_prob', 0.0)) for s in segment_list) / len(segment_list)
    avg_logprob = sum(float(getattr(s, 'avg_logprob', 0.0)) for s in segment_list) / len(segment_list)
    if peak >= 0.98 and avg_no_speech >= 0.55:
        return True
    if peak >= 0.95 and avg_logprob <= -1.0:
        return True
    if avg_no_speech >= 0.65 and avg_logprob <= -0.8:
        return True
    if avg_logprob <= -1.2:
        return True
    return False


def is_likely_decrypt_noise(*, peak: float, rms: float, duration: float) -> bool:
    """Heuristic for saturated PCM from failed DAVE decrypt."""
    if peak < 0.98:
        return False
    if rms < 0.05:
        return False
    if duration < 0.2:
        return False
    return True


def is_short_clip_hallucination(text: str, *, duration: float) -> bool:
    """Reject canned phrases Whisper invents on very short marginal VC audio."""
    if duration >= 2.5:
        return False
    normalized = re.sub(r"[^\w\s']+", ' ', (text or '').lower())
    normalized = ' '.join(normalized.split())
    if not normalized:
        return True
    for phrase in _SHORT_CLIP_HALLUCINATIONS:
        if normalized == phrase or normalized.startswith(phrase):
            return True
    return False


def segments_to_transcript(segment_list) -> tuple[str, list]:
    """Build transcript from Whisper segments, dropping low-confidence tails."""
    kept = []
    for segment in segment_list:
        text = str(getattr(segment, 'text', '') or '').strip()
        if not text:
            continue
        if float(getattr(segment, 'no_speech_prob', 0.0)) >= 0.65:
            continue
        if float(getattr(segment, 'avg_logprob', 0.0)) < -1.05:
            continue
        kept.append((text, float(getattr(segment, 'avg_logprob', 0.0))))

    while kept and kept[-1][1] < -0.75 and len(kept[-1][0]) <= 4:
        kept.pop()

    text = ' '.join(part for part, _logprob in kept).strip()
    return text, kept


def reject_discord_transcript(
    text: str,
    *,
    peak: float = 0.0,
    duration: float = 0.0,
    segment_list=None,
) -> tuple[bool, str]:
    """Return (rejected, reason)."""
    cleaned = str(text or '').strip()
    if not cleaned:
        return True, 'empty'
    if duration > 0 and is_short_clip_hallucination(cleaned, duration=duration):
        return True, 'short_clip_hallucination'
    if is_whisper_hallucination(cleaned):
        return True, 'hallucination'
    if is_garbage_tail_transcript(cleaned):
        return True, 'garbage_tail'
    if is_repetitive_noise_transcript(cleaned):
        return True, 'repetitive_noise'
    if _dominant_noise_phrase(cleaned):
        return True, 'noise_phrase'
    if segment_list and is_low_confidence_segments(segment_list, peak=peak):
        return True, 'low_confidence'
    if peak >= 0.98 and len(cleaned) > 80 and is_repetitive_noise_transcript(cleaned[:80]):
        return True, 'clipped_repetitive'
    return False, ''
