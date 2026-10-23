"""Streaming chunkifier for TTS — turns an LLM token stream into speakable
chunks at sentence/paragraph boundaries (with max-char fallback).

Used by the v2.7.0 streaming TTS pipeline. Pure-Python: no Sapphire imports,
no I/O, no Kokoro. Unit-testable in isolation.

Behavior:
- Strips `<think>...</think>`, `<reasoning>...</reasoning>`, `<tools>...</tools>`
  blocks and ```code fences``` from the stream — even when the opening tag
  arrives in one token and the closer arrives in a later token.
- Detects sentence ends via `.!?` followed by whitespace+capital
  (so `Dr. Smith`, `3.14`, `3:45 PM`, URLs don't split).
- Paragraph breaks (`\\n\\n`) always split.
- Ellipsis (`...`) is ordinary text — its final dot splits like a period in
  sentence mode; Kokoro renders the trail-off pause from the dots.
- Secondary punctuation (`;` `:`) splits only when the chunk has already
  grown past max_chars * 0.5 — avoids tiny chunks on natural prose pauses.
- If no boundary appears within `max_chars`, force-splits on the last
  whitespace inside the window.
- Empty/all-stripped stream yields no chunks.

Each yielded chunk carries metadata for the downstream player (boundary
type + suggested post-chunk pause).
"""
import logging
import re
from typing import Iterable, Iterator, Optional, Tuple

log = logging.getLogger(__name__)

# Pause duration (milliseconds) the player honors after each boundary type.
# Originally tuned for "natural prose pacing" (sentence=250 etc.) but with
# browser Audio() element startup latency also stacking ~100-300ms per
# chunk, it felt artificially gappy. Kokoro's synthesized audio already
# has natural breath silence at sentence ends, so the player can re-trigger
# almost immediately and the speech still sounds normal. Lowered 2026-05-18.
PAUSE_AFTER_MS = {
    "sentence":   0,     # . ! ? — browser's natural play() startup (~20-50ms)
                         # already provides breathing room on fast hardware.
                         # Was 30ms — dropped 2026-05-21 because the 30ms stacked
                         # on top of HTML5 Audio element startup, producing an
                         # audible ~100ms gap on fast hardware where the
                         # natural pause alone is enough.
    "secondary":  0,     # ; : — gapless
    "paragraph":  80,    # \n\n — clear paragraph break (was 200; dropped 2026-05-21,
                         # same rationale as sentence — natural startup adds latency)
    "maxlen":     0,     # mid-thought split, no pause
    "end":        0,     # final flush at end of stream
}

# Block-level openers that are hidden entirely. Each (opener, closer) pair.
BLOCK_TAGS: list = [
    ("<think>", "</think>"),
    ("<reasoning>", "</reasoning>"),
    ("<tools>", "</tools>"),
]

# Secondary punctuation splits when the chunk has grown past this fraction
# of max_chars. Avoids splitting "Yes:" into its own tiny chunk.
_SECONDARY_SPLIT_FRACTION = 0.5


class SpeechChunker:
    """Push-based variant of chunkify_for_speech. Maintains internal state
    across push() calls — useful when you're inside an existing token
    loop (e.g. the LLM streaming loop in chat_streaming.py) and want to
    integrate chunking without restructuring as a generator-of-a-generator.

    Same semantics as chunkify_for_speech:
        chunker = SpeechChunker()
        for token in token_stream:
            for chunk in chunker.push(token):
                handle(chunk)
        for chunk in chunker.flush():
            handle(chunk)

    split_mode controls chunk granularity:
      'sentence'  — break on every sentence end (lower latency, but loses
                    prosodic flow between sentences in same paragraph).
      'paragraph' — break only on \\n\\n paragraph breaks (preserves
                    intonation across consecutive sentences). max_chars
                    still applies as a safety cap so a long paragraph
                    doesn't buffer forever. DEFAULT.
    pause_overrides: dict mapping boundary name → ms (overrides PAUSE_AFTER_MS).
    """

    def __init__(self, max_chars: int = 750, min_chars: int = 15,
                 split_mode: str = "paragraph", pause_overrides: Optional[dict] = None,
                 stage_pause_style: str = "comma"):
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.split_mode = split_mode if split_mode in ("paragraph", "sentence") else "paragraph"
        self.pause_overrides = pause_overrides or {}
        # Stage direction prosody: how to mark *X* and (X) for Kokoro.
        # Kokoro renders comma/period/ellipsis as natural prosodic pauses.
        # 'none' = strip cleanly (legacy). 'comma' = gentle breath (default).
        self.stage_pause_style = stage_pause_style if stage_pause_style in (
            "none", "comma", "period", "ellipsis"
        ) else "comma"
        self._raw_buf = ""
        self._clean_buf = ""
        self._chunk_index = 0

    def push(self, token: str) -> list:
        """Push a token. Returns any chunks now ready to emit."""
        if not token:
            return []
        self._raw_buf += token
        safe_len = _safe_prefix_len(self._raw_buf)
        if safe_len > 0:
            piece = self._raw_buf[:safe_len]
            self._raw_buf = self._raw_buf[safe_len:]
            self._clean_buf += _clean_piece(piece)
        return self._drain()

    def flush(self) -> list:
        """End-of-stream — emit any remaining chunks (with relaxed min_chars
        on the final chunk since no more tokens are coming)."""
        out: list = []
        # Process leftover raw — drop unclosed tags
        if self._raw_buf:
            leftover = self._raw_buf
            for opener, _closer in BLOCK_TAGS:
                if opener in leftover:
                    idx = leftover.find(opener)
                    leftover = leftover[:idx]
            if leftover.count("```") % 2 == 1:
                leftover = leftover[:leftover.rfind("```")]
            self._clean_buf += _clean_piece(leftover)
            self._raw_buf = ""
        out.extend(self._drain())
        # Final chunk for whatever's left (regardless of min_chars)
        final_text = self._clean_buf.strip()
        if final_text:
            out.append(_make_chunk(final_text, "end", self._chunk_index, self.pause_overrides))
            self._chunk_index += 1
            self._clean_buf = ""
        return out

    def _drain(self) -> list:
        """Pull as many chunks as possible out of clean_buf with current params."""
        # Apply stage-direction prosody substitution before splitting.
        # Complete *X* and (X) pairs get wrapped with the configured marker;
        # unclosed ones remain as-is and get processed when the closer arrives.
        self._clean_buf = _apply_stage_prosody(self._clean_buf, self.stage_pause_style)
        out: list = []
        while True:
            cut = _find_split(self._clean_buf, self.max_chars, self.min_chars, self.split_mode)
            if cut is None:
                break
            chunk_text, boundary, remainder = cut
            chunk_text = chunk_text.strip()
            self._clean_buf = remainder
            if not chunk_text:
                continue
            out.append(_make_chunk(chunk_text, boundary, self._chunk_index, self.pause_overrides))
            self._chunk_index += 1
        return out


def chunkify_for_speech(
    token_stream: Iterable[str],
    max_chars: int = 750,
    min_chars: int = 15,
    split_mode: str = "paragraph",
    pause_overrides: Optional[dict] = None,
    stage_pause_style: str = "comma",
) -> Iterator[dict]:
    """Consume an arbitrary token stream, yield speakable chunks.

    Thin wrapper around SpeechChunker for use cases where the whole token
    stream is available as an iterable. See SpeechChunker docstring for
    the push-based API used by streaming integrations.

    Yields:
        dict per chunk:
          {
            "text": str,            # cleaned, ready for TTS
            "boundary": str,        # 'sentence'|'paragraph'|
                                    #  'secondary'|'maxlen'|'end'
            "pause_after_ms": int,  # suggested gap before next chunk
            "index": int,           # 0-indexed within this stream
          }
    """
    chunker = SpeechChunker(
        max_chars=max_chars, min_chars=min_chars,
        split_mode=split_mode, pause_overrides=pause_overrides,
        stage_pause_style=stage_pause_style,
    )
    for token in token_stream:
        for chunk in chunker.push(token):
            yield chunk
    for chunk in chunker.flush():
        yield chunk


def _make_chunk(text: str, boundary: str, index: int, pause_overrides: Optional[dict] = None) -> dict:
    # Collapse whitespace inside the chunk for clean TTS input
    text = re.sub(r"\s+", " ", text).strip()
    # Clamp pause to [0, 2000ms]. A misbehaving plugin's `tts_chunk_text`
    # hook can't add a custom pause (that's metadata-only) but a future
    # custom-boundary type could yield a bogus value and freeze playback.
    # Belt-and-suspenders. 2026-05-18 herring-table #13.
    pauses = dict(PAUSE_AFTER_MS)
    if pause_overrides:
        pauses.update(pause_overrides)
    pause = pauses.get(boundary, 0)
    pause = max(0, min(2000, int(pause)))
    return {
        "text": text,
        "boundary": boundary,
        "pause_after_ms": pause,
        "index": index,
    }


def _safe_prefix_len(raw: str) -> int:
    """Return the length of the prefix of `raw` that we can safely process.
    Anything past this position might be inside a half-formed tag/fence and
    should wait for more tokens."""
    n = len(raw)
    safe = n

    # Block tags — find any opener without a matching closer
    for opener, closer in BLOCK_TAGS:
        idx = 0
        while True:
            o = raw.find(opener, idx)
            if o < 0:
                break
            c = raw.find(closer, o + len(opener))
            if c < 0:
                safe = min(safe, o)
                break
            idx = c + len(closer)

    # Code fences — count triple-backticks; if odd, last one is unclosed
    fence_positions = []
    idx = 0
    while True:
        f = raw.find("```", idx)
        if f < 0:
            break
        fence_positions.append(f)
        idx = f + 3
    if len(fence_positions) % 2 == 1:
        safe = min(safe, fence_positions[-1])

    # Trailing `<` without `>` → partial HTML / avatar tag. Hold back.
    # Back up over a run of consecutive `<` so a doubled opener like the
    # avatar token `<<avatar:` is held WHOLE — not split into a leaked `<`
    # (flushed now) plus a buffered remainder that later synthesizes a stray
    # bracket. Normal prose has no `<<`, so this only affects `<<`-openers.
    last_lt = raw.rfind("<", 0, safe)
    if last_lt >= 0 and raw.find(">", last_lt, safe) < 0:
        while last_lt > 0 and raw[last_lt - 1] == "<":
            last_lt -= 1
        safe = min(safe, last_lt)

    # Trailing 1-2 backticks could become a triple → hold back
    s = raw[:safe]
    while s.endswith("`") and not s.endswith("```"):
        # If 2 trailing backticks, also hold (could become ``` next token)
        safe -= 1
        s = raw[:safe]
        if not s.endswith("`"):
            break

    return max(0, safe)


# Avatar presentation tokens — `<<avatar: track>>`, `<<avatar: track loop>>`,
# `<<avatar: track once>>`, `<<avatar: track 2.5s>>`. These are a display-layer
# protocol: the frontend strips them for display and fires the animation; they
# must NOT reach the synthesizer. Mirrors the frontend scanner regex so TTS
# strips exactly what display strips. Listed FIRST in _INLINE_PATTERNS so the
# `<[^>]+>` catch-all below never sees the token and mangles it into a stray
# `>` that gets spoken. 2026-05-28.
_AVATAR_RE = re.compile(r"<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>")

# Inline cleanup for safe-prefix pieces. Block tags should already be
# handled via safe-prefix gating; these patterns are belt-and-suspenders
# plus inline markdown handling.
#
# Note: stars (*X*) and parens ((X)) are NOT stripped here — they're handled
# by _apply_stage_prosody in _drain so we can wrap them with prosodic
# markers (commas/periods/ellipses) before stripping. Kokoro renders comma
# pauses naturally; stripping stars early left stage directions inline with
# no prosodic cue, which sounded flat.
_INLINE_PATTERNS = [
    (_AVATAR_RE, " "),                                      # avatar display tokens
    (re.compile(r"<think>.*?</think>", re.DOTALL), " "),
    (re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL), " "),
    (re.compile(r"<tools>.*?</tools>", re.DOTALL), " "),
    (re.compile(r"```[\s\S]*?```"), " "),
    (re.compile(r"`[^`]+`"), " "),                          # inline code
    (re.compile(r"!\[.*?\]\(.*?\)"), " "),                  # image markdown
    (re.compile(r"\|[^\n]*\|(?:\n\|[^\n]*\|)*"), " "),      # tables
    (re.compile(r"<[^>]+>"), " "),                          # complete HTML tag
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),          # markdown link → text
    (re.compile(r"(?<!\w)_+(?!\w)"), ""),                   # underscore emphasis
]

# Stage-prosody marker per style. (before, after) wrappers substituted around
# *X* and (X) content. Kokoro renders these as natural pauses; bare stars/
# parens it skips or renders flat.
_STAGE_MARKERS = {
    "none":     ("", ""),
    "comma":    (", ", ", "),
    "period":   (". ", ". "),
    "ellipsis": ("... ", "... "),
}

# Match *X* (bold/italic markdown) — one or more stars wrapping non-star
# content. Matches **bold**, *italic*, and any star-count Sapphire uses.
_STAR_WRAP_RE = re.compile(r"\*+([^*]+?)\*+")

# Match (X) — parenthetical content, single line. Non-greedy so nested
# parens still match the inner pair first. Bounded length (max 200 chars)
# so a runaway-open `(` can't swallow the rest of the buffer.
_PAREN_WRAP_RE = re.compile(r"\(([^()]{1,200})\)")


def _apply_stage_prosody(text: str, style: str) -> str:
    """Substitute *X* and (X) with prosodic markers per style.

    Runs on the accumulated clean_buf inside SpeechChunker._drain. Complete
    pairs get wrapped; unclosed *X (still streaming) stay as-is until the
    closer arrives in a later token. After substitution, any stray stars
    are stripped.
    """
    before, after = _STAGE_MARKERS.get(style, _STAGE_MARKERS["comma"])
    if before or after:
        text = _STAR_WRAP_RE.sub(lambda m: f"{before}{m.group(1).strip()}{after}", text)
        text = _PAREN_WRAP_RE.sub(lambda m: f"{before}{m.group(1).strip()}{after}", text)
    else:
        # 'none' style: strip stars/parens cleanly without prosodic markers.
        text = _STAR_WRAP_RE.sub(r"\1", text)
        text = _PAREN_WRAP_RE.sub(r"\1", text)
    # Strip any stray stars left over (unclosed mid-stream, double-wrapped, etc.)
    text = re.sub(r"\*+", "", text)
    # Collapse the comma/period doubling that substitution can produce
    # (e.g. ", , " or ". . ") into a single marker. REQUIRE whitespace
    # between repeated markers — otherwise this nukes ellipsis ("...")
    # and run-on punctuation like "!!" that should be preserved. The
    # substitution-induced doublings always have a space between them
    # (stage markers are ", " or ". " with trailing space), so requiring
    # \s+ between consecutive markers is safe.
    # 2026-05-26 — fix for ellipsis being silently collapsed to ".".
    # Still load-bearing: the dots must reach Kokoro intact so it renders
    # the trail-off pause (ellipsis is no longer a split boundary, 2026-06-11).
    text = re.sub(r"(,\s+){2,}", ", ", text)
    text = re.sub(r"(\.\s+){2,}", ". ", text)
    return text


def _clean_piece(text: str) -> str:
    for pat, repl in _INLINE_PATTERNS:
        text = pat.sub(repl, text)
    return text


# Sentence boundary: . ! ? optionally followed by closing quote/paren,
# then whitespace, then uppercase letter. The [A-Z] lookahead guards
# against "Dr. Smith", "3.14", "3:45 PM" false splits.
# Capture group 1 = punctuation; group 2 = optional closing quotes/parens.
_SENTENCE_RE = re.compile(r"([\.!?])([\"'\)\]]*)\s+(?=[A-Z])")

# Casual sentence boundary: same shape but next char is lowercase. Required
# for Sapphire's casual register ("hello. and yeah.") and post-action runs
# ("*peeks out* and waves."). Guarded against abbreviations ("Dr.", "Mrs.",
# "etc.") by requiring at least 4 word-chars BEFORE the terminator — common
# abbreviations are ≤3 letters. False-positives still possible (4+ char
# abbreviations like "Calif.", "Mass."), accepted as cheap-fix tradeoff vs.
# the lockup-the-whole-buffer cost for lowercase-heavy responses. 2026-05-20.
_CASUAL_SENTENCE_RE = re.compile(r"(?<=\w{4})([\.!?])([\"'\)\]]*)\s+(?=[a-z])")

# CJK / Arabic / full-width terminators — split unconditionally on these.
# No abbreviation ambiguity in CJK conventions, and CJK text typically has
# no whitespace between sentences so `\s+` requirement would never match.
# Matches the terminator + any trailing closing punctuation. 2026-05-20.
_CJK_SENTENCE_RE = re.compile(r"([。！？؟])([\"'\)\]」』]*)")

# Paragraph: at least two newlines in a row (allow whitespace between).
_PARAGRAPH_RE = re.compile(r"\n\s*\n")

# Secondary: ; or : followed by whitespace + uppercase. Only splits when
# the chunk is already large (see _SECONDARY_SPLIT_FRACTION above).
_SECONDARY_RE = re.compile(r"([;:])\s+(?=[A-Z])")


def _find_split(buf: str, max_chars: int, min_chars: int,
                split_mode: str = "paragraph") -> Optional[Tuple[str, str, str]]:
    """Find earliest valid split point in buf. Returns (chunk, boundary,
    remainder) or None if no split is justified yet.

    split_mode='paragraph' (default) skips sentence/casual/secondary boundaries
    to preserve TTS prosody across consecutive sentences in same paragraph.
    Paragraph + max_chars cap still apply.

    split_mode='sentence' keeps the original (lower-latency) behavior splitting
    on every sentence end.
    """
    if len(buf) < min_chars:
        return None

    # 1. Paragraph break — but don't emit a chunk smaller than min_chars.
    # A 1-word paragraph ("Oh-") mid-reply would otherwise become its own
    # chunk: Kokoro renders it out of context AND it adds a stray pause. Scan
    # for the first paragraph break that leaves a chunk of at least min_chars;
    # a too-small leading paragraph merges FORWARD into the next one. (Whole
    # short replies like "Sure" have no paragraph break and are emitted by
    # flush() regardless of min_chars at end-of-stream, so they still render.)
    # 2026-05-28.
    for m in _PARAGRAPH_RE.finditer(buf):
        if len(buf[:m.start()].strip()) >= min_chars:
            return buf[:m.start()], "paragraph", buf[m.end():]

    # Ellipsis is NOT a boundary — `...` rides through as ordinary text and
    # the sentence regex below treats its final dot like any period. Kokoro
    # renders the trail-off pause from the dots themselves. The old special
    # case (split + 150ms pause) was a fossil from the 250ms-pause era and
    # broke "Fishy... You know" into two streams. Removed 2026-06-11.

    if split_mode == "sentence":
        # 3. Sentence: .!? followed by uppercase next (strict — abbreviation-safe)
        m = _SENTENCE_RE.search(buf)
        if m:
            end = m.end(2) if m.group(2) else m.end(1)
            return buf[:end], "sentence", buf[m.end():]

        # 3b. CJK / Arabic terminator — unconditional split, no whitespace required.
        m = _CJK_SENTENCE_RE.search(buf)
        if m:
            end = m.end(2) if m.group(2) else m.end(1)
            return buf[:end], "sentence", buf[m.end():]

        # 3c. Casual sentence: .!? followed by lowercase next. Same shape as #3
        # but for Sapphire's casual register. Abbreviation-guard via 4-char
        # lookbehind already baked into the regex. 2026-05-20.
        m = _CASUAL_SENTENCE_RE.search(buf)
        if m:
            end = m.end(2) if m.group(2) else m.end(1)
            return buf[:end], "sentence", buf[m.end():]

        # 4. Secondary: ; : — only if chunk is already big enough
        if len(buf) >= max_chars * _SECONDARY_SPLIT_FRACTION:
            m = _SECONDARY_RE.search(buf)
            if m:
                return buf[:m.end(1)], "secondary", buf[m.end():]

    # 5. Max-char fallback: split on the last whitespace inside the window.
    if len(buf) >= max_chars:
        cut = buf.rfind(" ", min_chars, max_chars)
        if cut >= 0:
            return buf[:cut], "maxlen", buf[cut:].lstrip()
        # No whitespace inside the window — text is unspaced (long URL,
        # chemical formula, base64 blob). Hold the buffer until it exceeds
        # 2× max_chars; only THEN cut mid-character as last resort. This
        # avoids slicing a 250-char URL into ugly "https://very-long-url-tha"
        # fragments when the rest of the URL would arrive in the next token.
        # 2026-05-18 herring-table #14.
        if len(buf) >= max_chars * 2:
            return buf[:max_chars], "maxlen", buf[max_chars:].lstrip()
        # else: wait for more tokens or for the upstream flush.

    return None
