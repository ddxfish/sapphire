"""Brain-side streaming TTS pump.

Wraps SpeechChunker + a small ThreadPoolExecutor to synthesize audio chunks
in the background while the LLM is still streaming tokens. Each finished
chunk becomes a `tts_chunk` SSE event. The pump is per-iteration: created
when streaming TTS is enabled+available, fed text from the LLM content
stream, and flushed at end-of-iteration to drain remaining audio.

Boundaries / chunking live in core.tts.streaming.SpeechChunker — this module
just orchestrates synth scheduling and event emission.
"""
import base64
import concurrent.futures
import logging
import queue
import re
import threading
import time
import uuid
from collections import deque
from typing import Callable, Optional

import config
from core.hooks import hook_runner, HookEvent
from core.tts.streaming import SpeechChunker

# Quick "is this worth synthesizing?" check — drops pure-emoji /
# pure-punctuation chunks before paying the synth round-trip. 2026-05-18
# fix for regen-with-emoji responses that were 400ing Kokoro.
# Unicode-aware: matches any letter in any script + digits, excludes
# underscore (which Kokoro would reject as noise). Originally Latin-only
# `[a-zA-Z0-9]`; widened 2026-05-20 so non-English content reaches the
# provider — Kokoro will still 400 unsupported scripts but the chunk
# accounting (_dropped_chunks) surfaces that visibly rather than dropping
# silently at the pump.
_HAS_SPEECH_RE = re.compile(r"[^\W_]", re.UNICODE)

logger = logging.getLogger(__name__)

# How long flush_and_close waits per pending future before polling the
# cancel_check. Short enough to feel snappy on user-stop, long enough
# to not burn CPU when synth is the normal-case bottleneck.
_FLUSH_POLL_TIMEOUT_S = 0.1
# Hard ceiling per future — if synth genuinely stalls (kokoro hung), bail.
_FLUSH_HARD_TIMEOUT_S = 30.0



class StreamingTTSPump:
    """Push-based pump: feed it LLM content text via `push(text)`, get back
    a list of SSE event dicts (zero-or-more `tts_chunk`s plus an initial
    `tts_stream_start` on the first push). Call `flush_and_close()` at end
    of stream to drain remaining synth + emit `tts_stream_end`.

    `cancel()` aborts outstanding work and fires `tts_stream_end` with
    `interrupted=True` — used when the user hits Stop.

    Hook surface (fired in this order, see docs/PLUGINS.md):
        tts_stream_start: once per turn, before any synth. metadata has
            `voice`, `speed`, `stream_id`. Plugin may set `skip_tts` to
            disable the whole turn's streaming TTS.
        tts_chunk_text:   once per chunk, before synth. `event.tts_text`
            is the chunk text (mutable). metadata: `chunk_index`,
            `boundary`, `pause_after_ms`, `stream_id`. `skip_tts` skips
            this single chunk.
        tts_chunk_audio:  once per chunk, after synth returns, before
            SSE emission. The audio carrier is a dict at
            `event.metadata['audio']` == {'audio_bytes', 'content_type'};
            mutate `metadata['audio']['audio_bytes']` (and optionally
            'content_type') to transform or replace the audio. metadata
            also has: `chunk_index`, `chunk_text`, `boundary`,
            `pause_after_ms`, `stream_id`.
        tts_stream_end:   once per turn. metadata: `chunk_count`,
            `total_chars`, `interrupted`, `stream_id`. Observational.
    """

    def __init__(self, system, cancel_check: Optional[Callable[[], bool]] = None,
                 voice_override: Optional[str] = None,
                 split_override: Optional[str] = None):
        self.system = system
        self.tts = getattr(system, "tts", None)
        self.provider = getattr(self.tts, "_provider", None) if self.tts else None
        # Explicit per-pump voice (e.g. /api/tts/stream `voice` param). Beats
        # the brain-override ContextVar and the global voice. None = unchanged
        # legacy chain — chat streaming never passes this.
        self.voice_override = (voice_override or "").strip() or None
        # Decide ONCE whether to use the streaming Kokoro endpoint
        # (multi-segment yield per chunk) or the legacy single-blob path.
        # Providers without a real streaming impl fall through to
        # provider.generate(). 2026-05-18 herring-table #7 — wires the
        # M2 endpoint that was previously orphaned in the brain pipeline.
        self._provider_streams = (
            self.provider is not None
            and hasattr(self.provider, "generate_stream")
            and getattr(self.provider, "supports_streaming", False)
        )
        # Voice/speed/pitch SNAPSHOT — bound ONCE at stream start. Chunks used
        # to read live client state at synth time, so a chat switch, a
        # continuity task's set→restore, or a second concurrent stream retuned
        # the remaining chunks of THIS stream mid-speech (2026-08-08).
        self._speed = getattr(self.tts, "speed", None) or 1.0
        self._pitch = getattr(self.tts, "pitch_shift", None) or 1.0
        self._default_voice = getattr(self.tts, "voice_name", None) or "af_heart"
        self._provider_pitch = getattr(self.provider, "supports_pitch", False)
        # Chunker bounds — user-tunable via Settings → TTS. Sensible
        # clamps so a typo in settings can't yield zero-size chunks or
        # unbounded buffers.
        min_chars = max(5, int(getattr(config, "TTS_STREAMING_MIN_CHARS", 15) or 15))
        max_chars = max(min_chars + 5, int(getattr(config, "TTS_STREAMING_MAX_CHARS", 750) or 750))
        # Split mode + pause overrides — see Settings → TTS → Streaming.
        # 'paragraph' (default) preserves prosody across sentences;
        # 'sentence' lowers latency at the cost of flatter prosody.
        # split_override: per-pump forcing (phone calls force 'sentence' —
        # paragraph mode defers ALL synth to end-of-generation on a typical
        # one-paragraph spoken reply, which is seconds of dead air on a call).
        split_mode = ((split_override or "").strip().lower()
                      or (getattr(config, "TTS_STREAMING_SPLIT_MODE", "paragraph") or "paragraph").strip().lower())
        pause_overrides = {
            "sentence":  int(getattr(config, "TTS_STREAMING_PAUSE_SENTENCE_MS", 0) or 0),
            "paragraph": int(getattr(config, "TTS_STREAMING_PAUSE_PARAGRAPH_MS", 80) or 80),
        }
        # Stage-direction prosody style — controls how *X* and (X) sound.
        # 'comma' adds gentle breath, 'period' adds full sentence-end pause,
        # 'ellipsis' adds longer narrative pause, 'none' = no marker (legacy).
        stage_pause_style = (getattr(config, "TTS_STREAMING_STAGE_PAUSE_STYLE", "comma") or "comma").strip().lower()
        self.chunker = SpeechChunker(
            max_chars=max_chars,
            min_chars=min_chars,
            split_mode=split_mode,
            pause_overrides=pause_overrides,
            stage_pause_style=stage_pause_style,
        )
        self.pending: deque = deque()
        self.executor = None
        self._stream_started = False
        self._closed = False
        # Stable id for this turn — plugins correlate events across hooks.
        self._stream_id = uuid.uuid4().hex
        self._chunk_count = 0
        self._total_chars = 0
        # Plugin can disable the whole turn via tts_stream_start skip_tts.
        self._skip_turn = False
        # External cancel signal (e.g. self.cancel_flag on the stream).
        # flush_and_close() polls this so a Stop pressed AFTER the LLM is
        # done but BEFORE all synth completes can short-circuit cleanly.
        self._cancel_check = cancel_check
        self._was_interrupted = False  # set when flush bails on cancel signal
        # Indexes of chunks that dropped on synth failure / hard-timeout.
        # flush_and_close emits a single SSE `notice` if any present, so
        # the user sees "lost audio for N chunk(s)" instead of silent gap.
        self._dropped_chunks: list = []

    @property
    def enabled(self) -> bool:
        return bool(
            getattr(config, "TTS_ENABLED", False)
            and getattr(config, "TTS_STREAMING_ENABLED", False)
            and self.provider is not None
            and getattr(self.provider, "supports_streaming", False)
        )

    def push(self, text: str) -> list:
        """Push LLM content text; return SSE event dicts to yield."""
        if not self.enabled or not text or self._closed or self._skip_turn:
            return []
        out: list = []
        if not self._stream_started:
            self._stream_started = True
            # Fire tts_stream_start hook — plugin can cancel whole turn.
            ev = self._fire_hook(
                "tts_stream_start",
                metadata={
                    "voice": getattr(self.tts, "voice_name", None),
                    "speed": getattr(self.tts, "speed", None),
                    "stream_id": self._stream_id,
                    "system": self.system,
                },
            )
            if ev and ev.skip_tts:
                logger.info(f"[TTS-STREAM] Plugin cancelled stream {self._stream_id} via tts_stream_start")
                # Fire tts_stream_end so plugins that opened state on start
                # (recording file, captions banner, etc.) can finalize. Without
                # this, plugin state across skip-turn cancels accumulates and
                # leaks. 2026-05-18 herring-table #12.
                self._fire_hook(
                    "tts_stream_end",
                    metadata={
                        "stream_id": self._stream_id,
                        "chunk_count": 0,
                        "total_chars": 0,
                        "interrupted": True,
                        "system": self.system,
                    },
                )
                self._skip_turn = True
                self._closed = True
                return []
            out.append({"type": "tts_stream_start", "stream_id": self._stream_id})
            self._executor()  # lazy-create
        for chunk in self.chunker.push(text):
            self._submit(chunk)
        out.extend(self._drain_ready())
        return out

    def flush_and_close(self):
        """Flush + block-drain remaining synth + emit `tts_stream_end`.

        Yields events incrementally as segments complete — on slow CPUs,
        this lets the browser start playing sentence 2 while sentence 3 is
        still synthesizing, instead of hoarding all events until every
        chunk finishes. 2026-05-20.

        Polls `cancel_check` (if provided) between futures so a Stop
        pressed during the drain (LLM done, synth still finishing) bails
        cleanly — the pending in-flight thread will still complete its
        HTTP call but we discard the result and fire the end hook with
        interrupted=True. No more thread leaks than `cancel()` alone."""
        if not self._stream_started or self._closed:
            self._close()
            return
        for chunk in self.chunker.flush():
            self._submit(chunk)
        interrupted = False
        # Drain pump-chunks in order. For each, fully drain its segment
        # queue (waiting on the worker if needed), polling cancel between
        # checks so a user Stop bails within ~100ms.
        while self.pending:
            if self._is_cancelled():
                interrupted = True
                break
            seg_queue, done_evt, fut, meta = self.pending[0]
            chunk_idx = meta.get("index")
            deadline = time.monotonic() + _FLUSH_HARD_TIMEOUT_S
            # Cumulative counter on meta — picks up any segments already
            # emitted by _drain_ready for this same chunk during prior
            # push() calls. Without this, flush_and_close would log only
            # its own slice of the total. 2026-05-26.
            meta.setdefault("segments_emitted", 0)
            while True:
                if self._is_cancelled():
                    interrupted = True
                    break
                # Block on the queue up to the poll interval — wakes
                # IMMEDIATELY when a segment is put. After processing a
                # segment, peek done_evt + empty to break without wasting
                # another 100ms wait on a finished worker.
                try:
                    segment = seg_queue.get(timeout=_FLUSH_POLL_TIMEOUT_S)
                except queue.Empty:
                    # Empty + worker done = we're finished with this chunk.
                    if done_evt.is_set():
                        break
                    if time.monotonic() > deadline:
                        logger.warning(
                            f"[TTS-STREAM] flush hard-timeout for chunk {chunk_idx}"
                        )
                        self._dropped_chunks.append(chunk_idx)
                        break
                    continue
                event = self._build_chunk_event(segment, meta)
                if event:
                    yield event
                    meta["segments_emitted"] += 1
                # Fast-exit: if worker has finished AND queue is now empty,
                # this chunk is fully drained. Avoids the wasted 100ms wait
                # after the LAST segment of a fast synth.
                if done_evt.is_set() and seg_queue.empty():
                    break
            if interrupted:
                break
            # Final post-done drain (race-safe against last put before set)
            while True:
                try:
                    segment = seg_queue.get_nowait()
                except queue.Empty:
                    break
                event = self._build_chunk_event(segment, meta)
                if event:
                    yield event
                    meta["segments_emitted"] += 1
            # If this chunk yielded zero playable segments, count it as
            # dropped (Kokoro 400 / network / decode failure all land here)
            total = meta["segments_emitted"]
            if total == 0:
                self._dropped_chunks.append(chunk_idx)
                logger.warning(
                    f"[TTS-STREAM] chunk {chunk_idx} dropped: zero playable segments emitted"
                )
            else:
                logger.info(
                    f"[TTS-STREAM] chunk {chunk_idx} emitted {total} segment(s)"
                )
            self.pending.popleft()
        # Cancel remaining workers on interrupt
        if interrupted:
            for _seg_queue, _done_evt, fut, _meta in self.pending:
                try:
                    fut.cancel()
                except Exception:
                    pass
            self.pending.clear()
        self._fire_hook(
            "tts_stream_end",
            metadata={
                "stream_id": self._stream_id,
                "chunk_count": self._chunk_count,
                "total_chars": self._total_chars,
                "interrupted": interrupted,
                "system": self.system,
            },
        )
        logger.info(
            f"[TTS-STREAM] stream {self._stream_id[:8]} done: "
            f"{self._chunk_count} chunks emitted, "
            f"{len(self._dropped_chunks)} dropped, "
            f"{self._total_chars} chars, interrupted={interrupted}"
        )
        # Surface drops as a notice — single SSE message regardless of how
        # many chunks fell. Avoids spamming the toast lane for a flaky run.
        # Wording leads with the most common cause (unsupported characters
        # in the text — e.g. CJK, emoji, symbols outside the active voice's
        # phoneme set) rather than blaming server health. The synthesizer
        # is shared across users worldwide; Sapphire often emits non-English
        # characters that English-only voices can't render. Text itself is
        # always delivered — only the audio for those chunks is missing.
        if self._dropped_chunks and not interrupted:
            n = len(self._dropped_chunks)
            chunk_word = "chunk" if n == 1 else "chunks"
            yield {
                "type": "notice",
                "severity": "warning",
                "message": (
                    f"{n} TTS {chunk_word} couldn't be voiced — likely contains "
                    f"characters or symbols the current voice doesn't support. "
                    f"Text is unaffected."
                ),
            }
        yield {
            "type": "tts_stream_end",
            "stream_id": self._stream_id,
            "chunk_count": self._chunk_count,
            "interrupted": interrupted,
        }
        self._was_interrupted = interrupted
        self._close()

    def _is_cancelled(self) -> bool:
        if self._cancel_check is None:
            return False
        try:
            return bool(self._cancel_check())
        except Exception:
            return False

    # _await_with_cancel and _CANCELLED sentinel — removed 2026-05-18 when
    # _synth switched from "return one bytes blob" to "stream segments via
    # Queue." The new flush_and_close drain loop blocks on queue.get with
    # cancel polling baked in directly. (herring-table #7 wiring)

    def cancel(self):
        """Drop in-flight synth; fire tts_stream_end(interrupted=True) so
        plugins can finalize state (e.g. close a recording file)."""
        if self._closed:
            return
        for _seg_queue, _done_evt, fut, _meta in self.pending:
            try:
                fut.cancel()
            except Exception:
                pass
        self.pending.clear()
        if self._stream_started:
            self._fire_hook(
                "tts_stream_end",
                metadata={
                    "stream_id": self._stream_id,
                    "chunk_count": self._chunk_count,
                    "total_chars": self._total_chars,
                    "interrupted": True,
                    "system": self.system,
                },
            )
        self._close()

    def _executor(self) -> concurrent.futures.ThreadPoolExecutor:
        if self.executor is None:
            # 2 workers = enough to pipeline against LLM token rate; more
            # would just contend on Kokoro's single-process bottleneck.
            self.executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="tts-stream",
            )
        return self.executor

    def _submit(self, chunk: dict):
        # Fire tts_chunk_text hook BEFORE synth — plugin can mutate text
        # or skip this chunk entirely via skip_tts.
        ev = self._fire_hook(
            "tts_chunk_text",
            tts_text=chunk["text"],
            metadata={
                "stream_id": self._stream_id,
                "chunk_index": chunk["index"],
                "boundary": chunk["boundary"],
                "pause_after_ms": chunk["pause_after_ms"],
                "system": self.system,
            },
        )
        if ev and ev.skip_tts:
            logger.debug(f"[TTS-STREAM] Plugin skipped chunk {chunk['index']} via tts_chunk_text")
            return
        # Plugin contract for tts_text:
        #   None         → plugin didn't set it (or no plugin) → use original
        #   ""/whitespace → plugin meant to mute this chunk → skip synth
        #   non-string   → plugin bug; log + use original (don't crash whole turn)
        #   string       → use the plugin's mutation
        # Old `or chunk["text"]` clobber silently restored original on empty
        # (defeating content-mod plugins) and crashed with TypeError on
        # non-string (killing the LLM turn). 2026-05-18 herring-table #4 #8.
        plugin_text = ev.tts_text if ev else None
        if plugin_text is None:
            text_to_synth = chunk["text"]
        elif not isinstance(plugin_text, str):
            logger.warning(
                f"[TTS-STREAM] Plugin returned non-string tts_text "
                f"({type(plugin_text).__name__}) for chunk {chunk['index']}; "
                f"using original text"
            )
            text_to_synth = chunk["text"]
        elif not plugin_text.strip():
            logger.info(
                f"[TTS-STREAM] Plugin emptied chunk {chunk['index']} text — "
                f"skipping synth"
            )
            return
        else:
            text_to_synth = plugin_text
        # Skip chunks that Kokoro will reject (no a-zA-Z0-9 = empty after
        # its whitelist filter = HTTP 400). Common case: chunks that are
        # pure emoji, unicode-only, or all-punctuation. We strip in
        # _clean_piece but markup-free emoji slips through. Skip silently
        # so the LLM's emoji-only sentences just don't get spoken (which
        # is what the user would expect anyway).
        if not _HAS_SPEECH_RE.search(text_to_synth or ""):
            logger.info(
                f"[TTS-STREAM] Skipping chunk {chunk['index']} — no speakable "
                f"chars (text={text_to_synth!r:.60})"
            )
            return
        meta = {
            "index": chunk["index"],
            "boundary": chunk["boundary"],
            "pause_after_ms": chunk["pause_after_ms"],
            "text": text_to_synth,
        }
        # Per-stream voice: a chat's stored `tts_voice` (e.g. a phone rule's
        # persona voice, patched by the twilio daemon) beats the global voice.
        # Resolved here in _submit — still inside the stream's context, so the
        # brain override ContextVar is visible.
        voice = self.voice_override
        if not voice:
            try:
                from core.chat.stream_brain import get_override
                _o = get_override()
                if _o:
                    voice = (_o.get("settings") or {}).get("tts_voice") or None
            except Exception:
                pass
        voice = voice or self._default_voice
        speed = self._speed
        logger.info(
            f"[TTS-STREAM] submit chunk {chunk['index']} "
            f"({chunk['boundary']}, {len(text_to_synth)} chars, "
            f"stream={self._provider_streams}): {text_to_synth!r:.80}"
        )
        # New shape: per-chunk Queue holds Kokoro pipeline segments as they
        # arrive over chunked-transfer. done flags when the worker has
        # finished iterating (or errored). Order preserved at the deque
        # level — segments from chunk N+1 don't emit until chunk N is fully
        # drained. 2026-05-18 herring-table #7.
        seg_queue: queue.Queue = queue.Queue()
        done_evt = threading.Event()
        fut = self._executor().submit(
            self._stream_synth, text_to_synth, voice, speed,
            seg_queue, done_evt, chunk["index"],
        )
        self.pending.append((seg_queue, done_evt, fut, meta))

    def _stream_synth(self, text: str, voice: str, speed: float,
                      seg_queue: "queue.Queue", done_evt: threading.Event,
                      chunk_index: int):
        """Worker-thread body: iterate provider.generate_stream() and push
        each segment into the chunk's Queue. On a non-streaming provider,
        fall back to a single generate() call and push one blob.

        The base class default of generate_stream yields a single blob
        from generate(), so this is also safe if a provider declares
        supports_streaming=True but ships the default impl."""
        n_segments = 0
        total_bytes = 0

        def _run(v):
            nonlocal n_segments, total_bytes
            kw = {"pitch": self._pitch} if self._provider_pitch else {}
            if self._provider_streams:
                for segment in self.provider.generate_stream(text, v, speed, **kw):
                    if segment:
                        seg_queue.put(segment)
                        n_segments += 1
                        total_bytes += len(segment)
            else:
                audio = self.provider.generate(text, v, speed, **kw)
                if audio:
                    seg_queue.put(audio)
                    n_segments += 1
                    total_bytes += len(audio)

        try:
            try:
                _run(voice)
            except Exception as e:
                logger.warning(
                    f"[TTS-STREAM] chunk {chunk_index} voice '{voice}' synth failed: {e!r}"
                )
            # Transient failure ≠ bad voice: retry the stream's OWN voice once
            # before degrading, so a hiccup doesn't swap the speaker for one
            # chunk mid-reply (2026-08-08 — "retries being weird").
            if n_segments == 0:
                try:
                    _run(voice)
                except Exception as e:
                    logger.warning(
                        f"[TTS-STREAM] chunk {chunk_index} same-voice retry failed: {e!r}"
                    )
            # A bad per-stream voice (wrong id, other provider's id, undownloaded
            # model) must degrade to the DEFAULT voice, not to silence — a muted
            # call reads as a dead line (2026-07-03: 'Eric' vs 'am_eric').
            # Default from the stream snapshot, not live state.
            default_voice = self._default_voice
            if n_segments == 0 and voice != default_voice:
                logger.warning(
                    f"[TTS-STREAM] chunk {chunk_index} produced nothing with "
                    f"'{voice}' — retrying with default '{default_voice}'"
                )
                _run(default_voice)
            logger.info(
                f"[TTS-STREAM] chunk {chunk_index} synth done: voice={voice} "
                f"streams={self._provider_streams} {n_segments} segments, {total_bytes} bytes"
            )
        except Exception as e:
            logger.warning(
                f"[TTS-STREAM] chunk {chunk_index} synth raised after "
                f"{n_segments} segments / {total_bytes} bytes: {e!r}"
            )
        finally:
            done_evt.set()

    def _drain_ready(self) -> list:
        """Non-blocking: drain Kokoro pipeline segments from the front-of-deque
        chunk's queue, emitting one tts_chunk SSE event per segment. Advance
        to the next pump-chunk only when the current one's done flag is set
        AND its queue is empty — preserves global segment order."""
        out: list = []
        while self.pending:
            seg_queue, done_evt, fut, meta = self.pending[0]
            # Cumulative counter on meta — survives across push() calls.
            # A chunk whose segments arrive split across multiple push()
            # invocations would otherwise reset the count between calls
            # and undercount (or, if all segments arrived in a PRIOR call
            # and the worker finishes between calls, falsely account a
            # successfully-emitted chunk as dropped). 2026-05-26.
            meta.setdefault("segments_emitted", 0)
            # Drain any segments already in the queue
            while True:
                try:
                    segment = seg_queue.get_nowait()
                except queue.Empty:
                    break
                event = self._build_chunk_event(segment, meta)
                if event:
                    out.append(event)
                    meta["segments_emitted"] += 1
            # Only advance to next chunk when this one is fully done
            if done_evt.is_set() and seg_queue.empty():
                # Mirror flush_and_close drop accounting: a chunk that
                # produced ZERO playable segments (Kokoro 400, decode fail,
                # plugin nulled audio_bytes) counts as a drop so the
                # end-of-stream notice surfaces it. Was a `pass` no-op until
                # 2026-05-20 — mid-stream drops were silently invisible.
                idx = meta.get("index")
                total = meta["segments_emitted"]
                if total == 0:
                    self._dropped_chunks.append(idx)
                    logger.warning(
                        f"[TTS-STREAM] chunk {idx} dropped: zero playable segments emitted"
                    )
                else:
                    logger.info(
                        f"[TTS-STREAM] chunk {idx} emitted {total} segment(s)"
                    )
                self.pending.popleft()
            else:
                break  # hold here — preserve order
        return out

    def _build_chunk_event(self, audio_bytes, meta: dict):
        """Fire tts_chunk_audio hook (mutable bytes), then build the SSE
        event dict. Returns None when audio is empty, non-bytes (e.g. a
        misbehaving provider returned junk), or a plugin nulls it."""
        if not audio_bytes or not isinstance(audio_bytes, (bytes, bytearray)):
            return None
        content_type = getattr(self.provider, "audio_content_type", "audio/ogg")
        # Hook event uses a dict carrier so plugins can mutate or replace.
        carrier = {"audio_bytes": audio_bytes, "content_type": content_type}
        self._fire_hook(
            "tts_chunk_audio",
            metadata={
                "stream_id": self._stream_id,
                "chunk_index": meta["index"],
                "chunk_text": meta["text"],
                "boundary": meta["boundary"],
                "pause_after_ms": meta["pause_after_ms"],
                "audio": carrier,
                "system": self.system,
            },
        )
        final_bytes = carrier.get("audio_bytes")
        final_ct = carrier.get("content_type") or content_type
        if not final_bytes:
            return None
        self._chunk_count += 1
        self._total_chars += len(meta["text"] or "")
        return {
            "type": "tts_chunk",
            "audio_b64": base64.b64encode(final_bytes).decode("ascii"),
            "content_type": final_ct,
            "index": meta["index"],
            "boundary": meta["boundary"],
            "pause_after_ms": meta["pause_after_ms"],
            "text": meta["text"],
            "stream_id": self._stream_id,
        }

    def _fire_hook(self, hook_name: str, tts_text: str = None, metadata: dict = None):
        """Fire a hook if any handlers exist. Returns the (possibly mutated)
        event for callers to read tts_text / skip_tts back, or None if no
        handlers were registered (cheap no-op path)."""
        if not hook_runner.has_handlers(hook_name):
            return None
        ev = HookEvent(
            tts_text=tts_text,
            config=config,
            metadata=metadata or {},
        )
        try:
            hook_runner.fire(hook_name, ev)
        except Exception as e:
            logger.warning(f"[TTS-STREAM] {hook_name} hook fire failed: {e!r}")
        return ev

    def _close(self):
        if self._closed:
            return
        self._closed = True
        if self.executor is not None:
            # cancel_futures=True (Py 3.9+) prevents queued-but-not-started
            # futures from running on shutdown — reduces socket leaks in
            # CLOSE_WAIT on Windows after many stop-mid-stream cycles.
            # 2026-05-18 herring-table #16.
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None
