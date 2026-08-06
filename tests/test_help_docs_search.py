"""search_help_docs — full-text search + unmarked-doc fallback (2026-08-05).

The AIX bug: search scanned only AI-marked content, so every doc without an
AI_INCLUDE_FULL marker or '## Reference for AI' section was invisible —
searching 'ghost message' whiffed while GHOST_MESSAGES.md sat in docs/, and
reading it by name errored. Search now scans raw files; unmarked reads
return the whole doc. Marked docs keep their read contract (AI section only).
"""
import pytest

from functions import docs


@pytest.fixture
def doc_dir(tmp_path, monkeypatch):
    d = tmp_path / "docs"
    d.mkdir()
    monkeypatch.setattr(docs, "DOCS_DIR", d)
    return d


def write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


def test_unmarked_doc_is_searchable(doc_dir):
    write(doc_dir, "GHOSTS.md", "# Ghost Messages\n\nA ghost message is invisible.\n")
    out, ok = docs.execute("search_help_docs", {"query": "ghost message"}, None)
    assert ok
    assert "[ghosts]" in out
    assert "invisible" in out          # snippet carries context


def test_unmarked_doc_is_readable_by_name(doc_dir):
    write(doc_dir, "GHOSTS.md", "# Ghost Messages\n\nA ghost message is invisible.\n")
    out, ok = docs.execute("search_help_docs", {"doc_name": "ghosts"}, None)
    assert ok, out                      # was: "has no Reference for AI section" error
    assert "invisible" in out


def test_search_covers_human_prose_of_marked_docs(doc_dir):
    write(doc_dir, "SPICE.md",
          "# Spice\n\nHumans: paprika lives in the human half only.\n\n"
          "## Reference for AI\n\nTerse AI notes about spice categories.\n")
    out, ok = docs.execute("search_help_docs", {"query": "paprika"}, None)
    assert ok
    assert "[spice]" in out


def test_marked_doc_read_contract_unchanged(doc_dir):
    write(doc_dir, "SPICE.md",
          "# Spice\n\nHumans: paprika lives in the human half only.\n\n"
          "## Reference for AI\n\nTerse AI notes about spice categories.\n")
    out, ok = docs.execute("search_help_docs", {"doc_name": "spice"}, None)
    assert ok
    assert "Terse AI notes" in out
    assert "paprika" not in out         # AI read still excludes human half
    full, ok2 = docs.execute("search_help_docs", {"doc_name": "spice", "full": True}, None)
    assert ok2 and "paprika" in full


def test_ranking_and_no_match_message(doc_dir):
    write(doc_dir, "ONE.md", "# One\n\nbanana\n")
    write(doc_dir, "MANY.md", "# Many\n\nbanana banana banana\n")
    out, ok = docs.execute("search_help_docs", {"query": "banana"}, None)
    assert ok
    assert out.index("[many]") < out.index("[one]")   # most hits first
    miss, ok3 = docs.execute("search_help_docs", {"query": "nothing-here"}, None)
    assert ok3 and "No matches" in miss and "one" in miss
