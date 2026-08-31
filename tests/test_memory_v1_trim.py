"""Legacy memory v1: over-cap saves trim at a word boundary instead of
refusing (2026-08-29 — the refusal sent her into a rewrite loop, 5 tool
calls per memory). Mirrors the Mind Palace contract."""
import pytest


@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    from plugins.memory.tools import memory_tools
    monkeypatch.setattr(memory_tools, "_db_path", tmp_path / "m.db", raising=False)
    monkeypatch.setattr(memory_tools, "_db_initialized", False, raising=False)
    memory_tools._ensure_db()
    return memory_tools


def test_over_cap_trims_and_saves(isolated_memory):
    mt = isolated_memory
    cap = mt.MAX_MEMORY_LENGTH
    long = ("word " * 130).strip()
    msg, ok = mt._save_memory(long, scope="default")
    assert ok is True and "Memory saved" in msg and "TRIMMED" in msg
    with mt._get_connection() as conn:
        rows = conn.execute("SELECT content FROM memories").fetchall()
    assert len(rows) == 1
    kept = rows[0][0]
    assert len(kept) <= cap and not kept.endswith("wor")
    dropped = long[len(kept):].strip()
    assert f'Dropped: "{dropped}"' in msg
    assert "delete_memory(" in msg and "save_memory" in msg


def test_at_cap_untouched(isolated_memory):
    mt = isolated_memory
    exact = "y" * mt.MAX_MEMORY_LENGTH
    msg, ok = mt._save_memory(exact, scope="default")
    assert ok and "TRIMMED" not in msg
    with mt._get_connection() as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == exact


def test_trim_helper_boundaries(isolated_memory):
    mt = isolated_memory
    cap = mt.MAX_MEMORY_LENGTH
    assert mt._trim_to_cap("a" * 500 + " " + "b" * 100) == ("a" * 500, "b" * 100)
    kept, dropped = mt._trim_to_cap("z" * (cap + 40))
    assert len(kept) == cap and len(dropped) == 40
    assert mt._trim_to_cap("  short  ") == ("short", "")
    # Early-whitespace region (hunt 2026-08-30 CRIT): a short label followed
    # by an unbroken run (CJK / base64 / URL) must hard-cut at the cap, not
    # save the label as a stub with the whole payload dropped.
    kept, dropped = mt._trim_to_cap("Note: " + "x" * 600)
    assert len(kept) == cap, f"stub save: kept only {len(kept)} chars"
    assert kept.startswith("Note: x")
    # Whitespace in the back half is still preferred (no word split).
    kept, dropped = mt._trim_to_cap("q" * 400 + " " + "r" * 200)
    assert kept == "q" * 400
