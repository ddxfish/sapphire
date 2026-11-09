"""Her mouth for each spoken segment, on every lane (browser, speakers, devices).

Read-only on the audio: never mutates it, never takes the chunk. Publishes
`avatar_mouth` for any renderer following her; ephemeral, a mouth curve from a
past turn is worth nothing to a late tab. A curve carries the avatar tags its
words carried (`cues`), timed into its audio, so every renderer fires them as
she says them, on its own playback clock.

Also: `avatar_voice` when a voice starts (a renderer then lets speech carry
this turn's tags instead of firing them off the text), and `avatar_tail` for
tags written after her last words.
"""
import logging

from core.event_bus import publish
from plugins.avatar import mouth

logger = logging.getLogger(__name__)
mouth.warm()
_segments = mouth.Segments()


def _enabled():
    try:
        from core.plugin_loader import plugin_loader
        return plugin_loader.get_plugin_state("avatar").get("enabled", True) is not False
    except Exception:
        return True


def tts_chunk_audio(event):
    meta = event.metadata or {}
    audio = (meta.get("audio") or {}).get("audio_bytes")
    if not audio or not _enabled():
        return
    text = meta.get("chunk_text") or ""
    sid, index, seg = meta.get("stream_id"), meta.get("chunk_index"), meta.get("segment") or 0
    try:
        dur = mouth.duration(audio)
        start, end = _segments.take(sid, index, seg, text, dur)
        curve = mouth.analyze(audio, text[start:end])
    except Exception as e:
        logger.warning(f"[avatar] mouth curve failed for chunk {index}.{seg}: {e!r}")
        return
    if not curve:
        return
    curve.update({
        "stream_id": sid,
        "index": index,
        "seg": seg,
        "plays_here": bool(meta.get("plays_here", True)),
        "device": meta.get("device"),
        "pause_after_ms": meta.get("pause_after_ms") or 0,
        "cues": mouth.cues(meta.get("avatar"), start, end, len(text), curve["dur"]),
    })
    publish("avatar_mouth", curve, ephemeral=True)


def tts_stream_start(event):
    meta = event.metadata or {}
    if _enabled():
        publish("avatar_voice", {"stream_id": meta.get("stream_id"),
                                 "plays_here": bool(meta.get("plays_here", True)),
                                 "device": meta.get("device")}, ephemeral=True)


def tts_stream_end(event):
    meta = event.metadata or {}
    _segments.end(meta.get("stream_id"))
    tail = meta.get("avatar_tail")
    if tail and _enabled():
        publish("avatar_tail", {"stream_id": meta.get("stream_id"),
                                "cues": [{"track": t["track"], "mode": t.get("mode", "once")} for t in tail]},
                ephemeral=True)
