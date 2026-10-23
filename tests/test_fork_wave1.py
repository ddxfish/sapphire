"""
Wave 1 fixes (fork-scopes-20260817.md): F3 (and, historically, F1 Stage A).

F1 Stage A's chat() wrapper — begin/end_streaming around the old blocking
engine — was SUPERSEDED by the 2026-08-17 dual-path merge: chat() is now a
consumer of chat_stream(), which counts itself at its own try-top, so the
non-streaming doors count as active streams structurally. Its coverage
lives in tests/test_dualpath_merge.py.

F3: TOOL_RESULT_MAX_CHARS head-preserving cap on tool-result text.
Head-preserving is mandatory: <<IMG::tool:id>> markers are PREPENDED and are
the only liveness reference keeping saved image bytes from the orphan-GC.

Run with: pytest tests/test_fork_wave1.py -v
"""
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import config as config_module
from core.chat.chat_tool_calling import cap_tool_result_text, _extract_tool_images


# =============================================================================
# F3 — head-preserving tool-result cap
# =============================================================================

class TestToolResultCap:
    def test_under_limit_unchanged(self, monkeypatch):
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 1000, raising=False)
        assert cap_tool_result_text("short result") == "short result"

    def test_over_limit_head_preserved_with_notice(self, monkeypatch):
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 1000, raising=False)
        out = cap_tool_result_text("A" * 5000, "mytool")

        assert out.startswith("A" * 1000)          # head kept verbatim
        assert "A" * 1001 not in out               # tail actually dropped
        assert "4000 chars truncated" in out
        assert "NOT a tool failure" in out
        assert "mytool" in out
        assert "images" in out                     # reassures images unaffected

    def test_zero_disables_cap(self, monkeypatch):
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 0, raising=False)
        big = "B" * 500_000
        assert cap_tool_result_text(big) == big

    def test_image_markers_survive_cap(self, monkeypatch):
        """Markers ride at the HEAD — a tail-keeping cap would orphan the
        image bytes for the GC sweep. This is the liveness guarantee."""
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 1000, raising=False)
        text = "<<IMG::tool:abc123def456.jpg>>\n" + "C" * 5000
        out = cap_tool_result_text(text, "screenshot")

        assert "<<IMG::tool:abc123def456.jpg>>" in out

    def test_extraction_caps_dict_results_and_keeps_markers(self, monkeypatch):
        """Integration: a tool returning {text, images} gets its marker
        prepended, then capped head-preserving — marker survives."""
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 1000, raising=False)
        history = MagicMock()
        result = {
            "text": "D" * 5000,
            "images": [{
                "data": base64.b64encode(b"fakeimagebytes").decode(),
                "media_type": "image/jpeg",
                "display_only": True,
            }],
        }
        text, llm_images = _extract_tool_images(result, history, None, "camera")

        assert "<<IMG::tool:" in text               # marker survived the cap
        assert "chars truncated" in text
        assert llm_images == []                     # display_only stays hidden
        history.save_tool_image.assert_called_once()

    def test_extraction_caps_plain_string_results(self, monkeypatch):
        monkeypatch.setattr(config_module, 'TOOL_RESULT_MAX_CHARS', 1000, raising=False)
        text, images = _extract_tool_images("E" * 5000, None, None, "bigread")

        assert text.startswith("E" * 1000)
        assert "chars truncated" in text
        assert images == []

    def test_default_shipped_is_200k(self):
        """Guard the shipped default — TOOL_RESULT_MAX_CHARS=200000 in the
        tools section of settings_defaults.json."""
        defaults = json.loads(
            (Path(__file__).parent.parent / "core" / "settings_defaults.json")
            .read_text(encoding="utf-8"))
        assert defaults["tools"]["TOOL_RESULT_MAX_CHARS"] == 200000
