"""Tripwires on the prompts-view source for the 2026-09-08 bulk-vault fix
(record: tmp/prompt-vault-bulk-plan.md). These read the shipped JS as text
and assert the load-bearing shapes are still there. They catch a revert or
a well-meaning "simplification" that quietly brings the five-minute batch
back — not behavior (that's the node corpus + the click-list).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "interfaces" / "web" / "static"


def _src(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def test_bulk_modal_uses_one_batch_request_not_a_client_loop():
    src = _src("views/prompts-cleanup.js")
    assert "vaultMoveBatch" in src
    # No per-item vaultMove loop anywhere in the cleanup module.
    assert not re.search(r"for \(const .* of chosen.*\)\s*\{[^}]*vaultMove\(", src, re.S)
    assert "vaultMove(" not in src.replace("vaultMoveBatch(", "")


def test_modal_save_waits_for_a_promise_returning_handler():
    src = _src("shared/modal.js")
    assert "typeof r.then === 'function'" in src
    assert "Working" in src


def test_prompts_view_keeps_the_editor_pane_scroll_and_guards_stale_paints():
    src = _src("views/prompts.js")
    assert ".pr-content')?.scrollTop" in src and ".pr-body')?.scrollTop" in src
    assert "_loadSeq" in src and "seq !== _loadSeq" in src
    assert "echoQuietUntil" in src and "'vault_changed' && Date.now() < echoQuietUntil" in src
    # The vault checkbox re-renders one accordion, not the world.
    vault_cb = src[src.index(".pr-piece-vault"):]
    vault_cb = vault_cb[:vault_cb.index("// Action buttons")]
    assert "renderAccordionBody(type)" in vault_cb and "await loadAll();" not in vault_cb


def test_filters_live_in_one_table():
    cleanup = _src("views/prompts-cleanup.js")
    for gone in ("function presetsHTML", "function wireFlatPresets", "function sectionedHTML",
                 "function wireSections", "function checkedRows"):
        assert gone not in cleanup, f"{gone} came back — filters must stay in checklist-modal.js"
    prim = _src("shared/checklist-modal.js")
    table = prim.split("export const FILTERS")[1].split("};")[0]
    for name in ("all", "none", "main", "custom"):
        assert f"{name}:" in table


def test_stock_pieces_ride_the_components_payload():
    assert "stock_pieces" in _src("shared/prompt-api.js")
    assert "stockPieces" in _src("views/prompts.js")
    route = (ROOT / "core" / "routes" / "content.py").read_text(encoding="utf-8")
    assert '"stock_pieces": _stock_piece_keys()' in route
