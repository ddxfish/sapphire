"""Plugin display-title resolution (2026-06-21).

The plugin-list title used to be the `description` field split on an em-dash —
so a normal prose description became a giant (clipped) "name" and you couldn't
tell plugins apart. Fix: a dedicated `short_display_name` field is the canonical
title; the old description-split survives only as a TRUNCATED (40-char) fallback,
so no plugin — ours, third-party, migrated or not — can ever show a paragraph.
"""
import core.api_fastapi  # noqa: F401 — load the app graph first; plugins.py imports
                          # api_fastapi at module top, so importing it standalone circular-fails
from core.routes.plugins import _plugin_display_title, _cap_title


def test_short_display_name_wins_over_description():
    out = _plugin_display_title(
        {"short_display_name": "Image Studio", "description": "Local image generation " * 9},
        "sd-server")
    assert out == "Image Studio"


def test_falls_back_to_display_name_then_short_name():
    assert _plugin_display_title({"display_name": "ElevenLabs"}, "elevenlabs") == "ElevenLabs"
    assert _plugin_display_title({"short_name": "HA"}, "homeassistant") == "HA"


def test_paragraph_description_is_capped_never_a_run_on():
    desc = "Local image generation via stable-diffusion.cpp (sd-server). Generic — runs Z-Image"
    out = _plugin_display_title({"description": desc}, "sd-server")
    assert len(out) <= 41          # 40 chars + the ellipsis
    assert out.endswith("…")
    assert "—" not in out           # took the pre-em-dash clause, then capped


def test_legacy_em_dash_convention_still_yields_short_title():
    assert _plugin_display_title({"description": "Weather — get the forecast"}, "weather") == "Weather"


def test_no_title_fields_falls_back_to_short_name_not_description():
    assert _plugin_display_title({}, "sd-server") == "sd-server"


def test_overlong_short_display_name_is_still_capped():
    out = _plugin_display_title({"short_display_name": "x" * 60}, "p")
    assert len(out) == 41 and out.endswith("…")


def test_cap_helper():
    assert _cap_title("short") == "short"
    assert _cap_title("x" * 50).endswith("…")
    assert len(_cap_title("x" * 50)) == 41
    assert _cap_title("  padded  ") == "padded"
