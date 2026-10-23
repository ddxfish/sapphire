"""Captioning demo — listens on the v2.7.0 streaming-TTS hook surface and
republishes chunk text to the event bus so a frontend banner can display
each spoken sentence in real time.

Three hooks declared in plugin.json:
    tts_stream_start  → fires once at start of a turn
    tts_chunk_text    → fires once per speakable chunk (sentence-ish)
    tts_stream_end    → fires once when the turn is done (or interrupted)
"""
import logging
from core.event_bus import publish

logger = logging.getLogger(__name__)


def tts_stream_start(event):
    """Stream is starting — clear any stale caption."""
    publish("captioning_start", {
        "stream_id": event.metadata.get("stream_id"),
    })


def tts_chunk_text(event):
    """A speakable chunk is about to be synthesized — surface its text.

    ephemeral=True: caption text is chat content — it must not sit in the
    SSE replay ring, where it outlives a vault lock and replays to any
    later tab (vaulted chats P3-T15)."""
    publish("captioning_chunk", {
        "stream_id": event.metadata.get("stream_id"),
        "chunk_index": event.metadata.get("chunk_index"),
        "text": event.tts_text or "",
        "boundary": event.metadata.get("boundary"),
        "pause_after_ms": event.metadata.get("pause_after_ms"),
    }, ephemeral=True)


def tts_stream_end(event):
    """Stream complete — frontend fades the banner."""
    publish("captioning_end", {
        "stream_id": event.metadata.get("stream_id"),
        "chunk_count": event.metadata.get("chunk_count"),
        "interrupted": bool(event.metadata.get("interrupted")),
    })
