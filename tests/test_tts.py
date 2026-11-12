"""Tests for TTS text cleaning (core/tts/tts_server.py clean_text)."""
import pytest
from _kokoro_stub import import_tts_server  # no GPU model, disk cache or network

clean_text = import_tts_server().clean_text


class TestCleanTextThinkBlocks:
    """Think block removal."""

    def test_removes_think_tags(self):
        assert "Hello" in clean_text("<think>internal</think>Hello")

    def test_removes_seed_think_tags(self):
        assert "Hello" in clean_text("<seed:think>internal</seed:think>Hello")

    def test_removes_multiline_think(self):
        text = "<think>\nline1\nline2\n</think>Result"
        assert "Result" in clean_text(text)
        assert "line1" not in clean_text(text)


class TestCleanTextHTMLStripping:
    """HTML tag removal."""

    def test_strips_html_tags(self):
        assert clean_text("<b>bold</b>") == "bold"

    def test_strips_nested_html(self):
        assert "text" in clean_text("<div><span>text</span></div>")


class TestCleanTextSmartQuotes:
    """Smart quote normalization - the bug we fixed."""

    def test_smart_single_quotes_to_straight(self):
        # U+2018 and U+2019 (left/right single quotes)
        result = clean_text("You\u2019re welcome")
        assert "You're welcome" == result

    def test_smart_double_quotes_to_straight(self):
        # U+201C and U+201D (left/right double quotes)
        result = clean_text("\u201CHello\u201D")
        assert '"Hello"' == result

    def test_mixed_smart_quotes(self):
        result = clean_text("She said \u201CYou\u2019re great\u201D")
        assert "She said" in result
        assert "You're great" in result

    def test_straight_quotes_preserved(self):
        result = clean_text("It's a \"test\"")
        assert "It's" in result


class TestCleanTextDashes:
    """Dash normalization - em/en dashes become period for TTS pause."""

    def test_em_dash_to_period(self):
        result = clean_text("Hello\u2014world")
        assert ". " in result

    def test_en_dash_to_period(self):
        result = clean_text("Hello\u2013world")
        assert ". " in result

    def test_double_hyphen_to_period(self):
        result = clean_text("Hello--world")
        assert ". " in result

    def test_hyphen_preserved(self):
        """Regular hyphens should NOT be replaced."""
        result = clean_text("well-known")
        assert "well-known" == result


class TestCleanTextEllipsis:
    """Ellipsis handling."""

    def test_unicode_ellipsis(self):
        result = clean_text("Wait\u2026 okay")
        assert "Wait" in result
        assert "\u2026" not in result

    def test_triple_dots(self):
        result = clean_text("Wait... okay")
        assert "..." not in result


class TestCleanTextWhitelist:
    """Character whitelist filtering."""

    def test_allows_alphanumeric(self):
        assert clean_text("Hello World 123") == "Hello World 123"

    def test_allows_punctuation(self):
        assert clean_text("Hello, world! How? Yes.") == "Hello, world! How? Yes."

    def test_strips_emoji(self):
        result = clean_text("Hello \U0001f600 world")
        assert "\U0001f600" not in result
        assert "Hello" in result

    def test_strips_special_unicode(self):
        result = clean_text("Price: 50\u20ac")
        assert "\u20ac" not in result

    def test_empty_after_cleaning(self):
        # All non-whitelisted chars
        assert clean_text("\U0001f600\U0001f600\U0001f600").strip() == ""

    def test_whitespace_normalization(self):
        result = clean_text("Hello    world")
        assert "  " not in result


class TestCleanTextBullets:
    """Bullet and special char replacement."""

    def test_bullet_to_space(self):
        result = clean_text("\u2022 Item one")
        assert "\u2022" not in result
        assert "Item one" in result

    def test_guillemets_to_quotes(self):
        result = clean_text("\u00ABquoted\u00BB")
        assert '"quoted"' == result


class TestCleanerHunt20261112:
    """TTS text fixes from the 2026-11-12 hunt."""

    def test_readable_symbols_and_accents_survive(self):
        out = clean_text("Caf\u00e9: 50% off, $5 + $5 = $10, 20\u00b0 and/or more")
        for piece in ("Caf\u00e9", "50%", "$5", "+", "=", "20\u00b0", "and/or"):
            assert piece in out

    def test_a_stray_less_than_does_not_eat_the_sentence(self):
        out = clean_text("if x < y then say hello, and z > 2 too")
        assert "then say hello" in out

    def test_stream_holds_a_tag_but_not_a_stray_bracket(self):
        from core.tts.streaming import _safe_prefix_len as safe
        raw = "I love you <3 and here is a long sentence that keeps going"
        assert safe(raw) == len(raw)                 # "<3" is not a tag: nothing held
        raw = "Hello there <spa"
        assert safe(raw) == raw.index("<")           # a tag being typed: held
