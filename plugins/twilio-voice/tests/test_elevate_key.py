"""Elevation passphrase matching (twilio-voice).

Regression guard for the 2026-07-15 hardening: fuzzy-0.8 on the whole normalized
string made the numeric suffix decorative — a bare word matched 'word3' at ~0.95.
Now the digit run must match EXACTLY (spoken-digit folding still applies) while
the word stays fuzzy for VOIP transcription noise.
"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "tools" / "elevate_tool.py"


def _load():
    spec = importlib.util.spec_from_file_location("twilio_elevate_undertest", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


el = _load()


# ── the hole that was closed ─────────────────────────────────────────────────
def test_bare_word_no_longer_matches_word_plus_number():
    assert el._key_matches("alligator", "alligator3") is False
    assert el._key_matches("alligator", "alligator37") is False


def test_wrong_number_fails_even_with_right_word():
    assert el._key_matches("alligator four", "alligator3") is False
    assert el._key_matches("alligator 5", "alligator3") is False


# ── legit callers still get in (VOIP tolerance preserved) ────────────────────
def test_spoken_digit_folding_matches():
    # The tool's `key` param is the ISOLATED passphrase (the LLM extracts it from
    # "...the key is alligator three"), so folding turns the number word to a digit.
    assert el._key_matches("alligator three", "alligator3") is True


def test_fuzzy_word_with_exact_number():
    # STT drops/garbles a letter but the number is heard right → still unlocks.
    assert el._key_matches("aligator3", "alligator3") is True


def test_exact_match():
    assert el._key_matches("swordfish42", "swordfish42") is True


# ── guards ───────────────────────────────────────────────────────────────────
def test_empty_and_missing():
    assert el._key_matches("", "alligator3") is False
    assert el._key_matches("alligator3", "") is False
    assert el._key_matches("", "") is False


def test_digit_only_key_needs_exact_digits():
    assert el._key_matches("one two three", "123") is True
    assert el._key_matches("one two four", "123") is False
