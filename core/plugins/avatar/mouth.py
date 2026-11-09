"""Her mouth for one spoken chunk: audio + its text -> a curve any renderer plays.

The curve is renderer-free: an envelope (how open), two bands (round vs wide, for
when there are no phonemes), and visemes laid over the voiced stretches of the
audio. The sidebar (three.js) and the screen renderer (Python) both read it, and
both map visemes to ARKit shapes through the one table in web/visemes.json.

English text gets phonemes from misaki (Kokoro's G2P, already in the env); words
it doesn't know and other languages fall back to letter rules, and with no text
at all the bands carry the mouth alone.
"""

import io
import json
import logging
import re
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

FPS = 50                          # curve frames a second
VISEMES = json.loads((Path(__file__).parent / "web" / "visemes.json").read_text(encoding="utf-8"))
VISEMES.pop("_about", None)

# misaki's alphabet -> viseme. A/I/O/W/Y are its diphthongs (eɪ aɪ oʊ aʊ ɔɪ),
# T its flap, ᵊ a light schwa. Stress marks and spaces are dropped.
PHONE = {
    'p': 'PP', 'b': 'PP', 'm': 'PP',
    'f': 'FF', 'v': 'FF',
    'θ': 'TH', 'ð': 'TH',
    't': 'DD', 'd': 'DD', 'T': 'DD',
    'k': 'kk', 'ɡ': 'kk', 'g': 'kk', 'ŋ': 'kk', 'h': 'kk',
    'ʧ': 'CH', 'ʤ': 'CH', 'ʃ': 'CH', 'ʒ': 'CH', 'j': 'I',
    's': 'SS', 'z': 'SS',
    'n': 'nn', 'l': 'nn',
    'ɹ': 'RR', 'r': 'RR', 'w': 'U',
    'ɑ': 'aa', 'æ': 'aa', 'ʌ': 'aa', 'a': 'aa', 'I': 'aa', 'W': 'aa',
    'ɛ': 'E', 'e': 'E', 'A': 'E', 'ə': 'E', 'ᵊ': 'E', 'ɜ': 'E',
    'i': 'I', 'ɪ': 'I',
    'ɔ': 'O', 'o': 'O', 'O': 'O', 'Y': 'O', 'ɒ': 'O',
    'u': 'U', 'ʊ': 'U',
}
# letter fallback for words G2P can't say (names, other languages)
LETTER = {
    'a': 'aa', 'e': 'E', 'i': 'I', 'y': 'I', 'o': 'O', 'u': 'U',
    'p': 'PP', 'b': 'PP', 'm': 'PP', 'f': 'FF', 'v': 'FF', 'w': 'U',
    't': 'DD', 'd': 'DD', 'k': 'kk', 'g': 'kk', 'c': 'kk', 'q': 'kk', 'h': 'kk', 'x': 'SS',
    'j': 'CH', 's': 'SS', 'z': 'SS', 'n': 'nn', 'l': 'nn', 'r': 'RR',
}
# relative length: vowels hold, diphthongs longer, stops are quick
WEIGHT = {'aa': 1.0, 'E': 0.9, 'I': 0.85, 'O': 1.0, 'U': 0.9, 'PP': 0.6, 'DD': 0.45,
          'kk': 0.5, 'nn': 0.55, 'SS': 0.7, 'CH': 0.7, 'FF': 0.65, 'TH': 0.6, 'RR': 0.6}
LONG = set('AIOWY')

_g2p = None
_g2p_lock = threading.Lock()


def warm():
    """Load misaki off the speech path (1-2 s the first time)."""
    threading.Thread(target=_load_g2p, name="avatar-g2p", daemon=True).start()


def _load_g2p():
    global _g2p
    with _g2p_lock:
        if _g2p is not None:
            return _g2p
        try:
            from misaki import en
            _g2p = en.G2P(trf=False, british=False, fallback=None)
        except Exception as e:                         # no misaki: letter rules only
            logger.info(f"[avatar] misaki unavailable, letter rules only: {e!r}")
            _g2p = False
        return _g2p


def _ascii_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return sum(c.isascii() for c in letters) / len(letters) if letters else 0.0


def phonemes(text):
    """Text -> [viseme, ...] in speaking order."""
    text = (text or "").strip()
    if not text:
        return []
    g2p = _g2p if _g2p is not None else None    # never block the speech path on a load
    out = []
    if g2p and _ascii_ratio(text) > 0.9:
        try:
            _, tokens = g2p(text)
            for tok in tokens:
                ps = tok.phonemes
                if ps:
                    for ch in ps:
                        v = PHONE.get(ch)
                        if v:
                            out.append((v, 1.4 if ch in LONG else 1.0))
                else:
                    out.extend(_letters(tok.text))
            return out
        except Exception as e:
            logger.debug(f"[avatar] g2p failed, letters instead: {e!r}")
    for word in re.findall(r"\w+", text):
        out.extend(_letters(word))
    return out


def _letters(word):
    out, last = [], None
    for ch in word.lower():
        v = LETTER.get(ch)
        if v and v != last:                         # 'll', 'ss' are one shape
            out.append((v, 1.0))
        last = v
    return out


def _decode(audio_bytes):
    import soundfile as sf
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def duration(audio_bytes):
    import soundfile as sf
    return float(sf.info(io.BytesIO(audio_bytes)).duration)


def _voiced(env, floor=0.12, bridge=4, shortest=2):
    """Frames where she is making sound -> [(start, end), ...] in frames."""
    on = env > floor
    spans, i, n = [], 0, len(env)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if spans and i - spans[-1][1] <= bridge:     # a tiny gap is one breath, not a pause
                spans[-1] = (spans[-1][0], j)
            else:
                spans.append((i, j))
            i = j
        else:
            i += 1
    return [s for s in spans if s[1] - s[0] >= shortest]


def analyze(audio_bytes, text=""):
    """One chunk -> {dur, fps, env, lo, hi, vis}. vis = [[t, viseme, dur], ...]."""
    pcm, sr = _decode(audio_bytes)
    if len(pcm) == 0:
        return None
    hop = max(1, sr // FPS)
    n = len(pcm) // hop
    if n == 0:
        return None
    frames = pcm[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = np.percentile(rms, 95) or 1.0
    env = np.clip(rms / peak, 0, 1) ** 0.6

    # two bands per frame: energy share under ~700 Hz (round) and over ~2.5 kHz (wide/sibilant)
    spec = np.abs(np.fft.rfft(frames * np.hanning(hop), axis=1))
    freqs = np.fft.rfftfreq(hop, 1 / sr)
    total = spec.sum(axis=1) + 1e-9
    lo = spec[:, freqs < 700].sum(axis=1) / total
    hi = spec[:, freqs > 2500].sum(axis=1) / total

    vis = []
    spans = _voiced(env)
    seq = phonemes(text)
    if spans and seq:
        voiced_frames = sum(e - s for s, e in spans)
        lengths = np.array([WEIGHT[v] * k for v, k in seq])
        lengths = lengths / lengths.sum() * voiced_frames      # frames each phoneme gets
        # walk the phonemes along the voiced stretches, skipping the silences
        si, pos = 0, spans[0][0]
        for (v, _), ln in zip(seq, lengths):
            while si < len(spans) and pos >= spans[si][1]:
                si += 1
                if si < len(spans):
                    pos = spans[si][0]
            if si >= len(spans):
                break
            vis.append([round(float(pos) / FPS, 3), v, round(float(ln) / FPS, 3)])
            pos += ln

    r = lambda a: [round(float(x), 2) for x in a]
    return {"dur": round(len(pcm) / sr, 3), "fps": FPS, "env": r(env), "lo": r(lo), "hi": r(hi), "vis": vis}


_SENT_END = re.compile(r"[.!?…]+[\"')\]]*\s+")
_CLAUSE_END = re.compile(r"[;:,]\s+")


class Segments:
    """Which words each audio segment of a chunk carries.

    A provider may voice one chunk as several segments (Audio8 per ~300-char
    sentence group, Kokoro past 510 phonemes, Piper per sentence) and says
    nothing about where it cut. So: the segment's length at her speaking rate
    says about how far it reached, snapped to the nearest sentence end (where
    every provider cuts). The rate learns from each finished chunk."""

    def __init__(self, cps=14.0):
        self.cps = cps
        self._open = {}                    # (stream, index) -> [text, pos, dur]
        self._lock = threading.Lock()

    def take(self, stream, index, seg, text, dur):
        """-> (start, end) char span of `text` this segment speaks."""
        with self._lock:
            key = (stream, index)
            if seg == 0 or key not in self._open:
                self._close_older(stream, index)
                self._open[key] = [text, 0, 0.0]
            st = self._open[key]
            start, rest = st[1], len(text) - st[1]
            est = dur * self.cps
            end = len(text)
            if rest > 0 and est < rest * 0.8:
                for pat in (_SENT_END, _CLAUSE_END):     # a sentence end near the guess, else a clause
                    cuts = [m.end() for m in pat.finditer(text, start) if m.end() < len(text) - 2]
                    best = min(cuts, key=lambda c: abs(c - start - est), default=None)
                    if best is not None and abs(best - start - est) <= est * 0.5:
                        end = best
                        break
            st[1], st[2] = end, st[2] + dur
            return start, end

    def _close_older(self, stream, index):
        """A newer chunk began: the ones before it are whole, so they teach the rate."""
        for k in [k for k in self._open if k[0] == stream and k[1] < index]:
            text, pos, dur = self._open.pop(k)
            if dur > 1.0 and pos >= len(text) and len(text) > 20:
                self.cps = 0.7 * self.cps + 0.3 * min(25.0, max(8.0, len(text) / dur))
        while len(self._open) > 16:
            self._open.pop(next(iter(self._open)))

    def end(self, stream):
        with self._lock:
            self._close_older(stream, 1 << 30)


def cues(tags, start, end, total, dur):
    """The tags inside this segment's span -> [{track, mode, t}], t in seconds."""
    out = []
    for tag in tags or ():
        p = tag.get("pos", 0)
        if start <= p < end or (end >= total and p >= end):
            frac = (p - start) / max(1, end - start)
            out.append({"track": tag["track"], "mode": tag.get("mode", "once"),
                        "t": round(min(dur, max(0.0, frac * dur)), 3)})
    return out
