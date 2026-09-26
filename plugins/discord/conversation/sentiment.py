"""RoBERTa tone for silent reactions (2026-09-26, Krem's vote A: no new pip deps).

cardiffnlp/twitter-roberta-base-sentiment-latest through transformers. torch is a
core requirement and transformers rides in with kokoro (core.vibes leans on the
same pair for CLIP), so nothing new is installed — the model itself (~500 MB)
lands in the HF cache on first use, offline-first after that, exactly like CLIP.
Import or load failure → compound() is None → the word list decides; logged once.
Never call this from the gateway loop: reactions.py scores at execute time, in a
thread, and the container warms the model on a thread at boot.
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

MODEL = 'cardiffnlp/twitter-roberta-base-sentiment-latest'
_pipe = None
_failed = False
_lock = threading.Lock()


def loaded() -> bool:
    return _pipe is not None


def _restore(prev) -> None:
    if prev is None:
        os.environ.pop('HF_HUB_OFFLINE', None)
    else:
        os.environ['HF_HUB_OFFLINE'] = prev


def _load():
    global _pipe, _failed
    if _pipe is not None or _failed:
        return _pipe
    with _lock:
        if _pipe is not None or _failed:
            return _pipe
        try:
            from transformers import pipeline
        except ImportError:
            logger.warning('[DISCORD] transformers not importable — reaction tone stays on the word list')
            _failed = True
            return None
        # Offline-first, the house rule for every cached model (core/vibes.py,
        # whisper, kokoro): a warm cache loads with zero network; the hub is
        # dialled once only when the files are missing.
        prev = os.environ.get('HF_HUB_OFFLINE')
        os.environ['HF_HUB_OFFLINE'] = '1'
        try:
            try:
                _pipe = pipeline('sentiment-analysis', model=MODEL, top_k=None, truncation=True)
            except (OSError, EnvironmentError):
                _restore(prev)
                logger.info('[DISCORD] RoBERTa tone model not cached — downloading once (~500 MB)')
                _pipe = pipeline('sentiment-analysis', model=MODEL, top_k=None, truncation=True)
            logger.info('[DISCORD] RoBERTa tone model loaded')
        except Exception as exc:
            logger.warning('[DISCORD] RoBERTa tone model failed to load (%s) — word list instead', exc)
            _failed = True
        finally:
            _restore(prev)
    return _pipe


def compound(text: str) -> float | None:
    """P(positive) − P(negative) in [-1, 1], or None when the model is unavailable."""
    pipe = _load()
    if not pipe:
        return None
    try:
        out = pipe(str(text or '')[:512])
        scores = out[0] if out and isinstance(out[0], list) else out
        by = {str(s.get('label', '')).lower(): float(s.get('score', 0.0)) for s in (scores or [])}
        return by.get('positive', 0.0) - by.get('negative', 0.0)
    except Exception as exc:
        logger.debug('[DISCORD] RoBERTa tone failed: %s', exc)
        return None


def warmup_async() -> None:
    """Load (and download once) on a daemon thread — at boot, not at the first reaction."""
    threading.Thread(target=_load, daemon=True, name='discord-tone-warmup').start()
