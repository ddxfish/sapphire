"""REGRESSION GUARDS — 2026-05-20 toolset-state corruption bug.

Critical reliability bug affecting experienced users: tool enabled in a custom
toolset, AI says "not in active toolset". Three independent failure paths,
three independent fixes, three independent regression tests.

Bug summary (the full story, so future-you understands what these tests guard):

  USER SYMPTOM
  ------------
  - User checks a tool's checkbox in a custom toolset.
  - AI tries to call it, executor rejects with "not in active toolset".
  - "Tools available in current toolset" list is short and alphabetically clipped.
  - After "jiggling knobs" (re-activating chat, opening another toolset and
    coming back), the tool starts working.
  - Took up to 18 LLM tool-call retries before recovery in one user's session.

  ROOT CAUSE #1 — UI double-write (this file's first test)
  --------------------------------------------------------
  interfaces/web/static/views/toolsets.js debouncedSave() fired TWO POSTs on
  every edit of an active toolset:
    1. POST /api/toolsets/custom  ← correct, triggers reapply_if_active
    2. POST /api/functions/enable ← REDUNDANT, corrupted current_toolset_name
                                    to "custom" because update_enabled_functions
                                    called with a multi-element list falls
                                    through to the "custom" branch at
                                    function_manager.py:740.
  Effect: current_toolset_name becomes "custom" even when editing a NAMED
  toolset. This disables the plugin auto-add path at function_manager.py:421
  (which only runs when current_toolset_name is NOT in ("none","custom")).
  Any plugin loaded AFTER the edit can't add its tools to _enabled_tools.

  ROOT CAUSE #2 — Boot guard blocks recovery (this file's second test)
  -------------------------------------------------------------------
  sapphire.py's post-scan toolset reapply was guarded by `current != "none"`.
  When the initial apply runs BEFORE plugins load, plugin-tool names are
  filtered out, and the toolset can land in the "none" fallback. The post-
  scan reapply was then SKIPPED by the guard, leaving the bad state to
  escape boot.

  ROOT CAUSE #3 — Server doesn't echo accepted list (this file's third test)
  --------------------------------------------------------------------------
  POST /api/toolsets/custom returned {status, name} — no canonical function
  list. UI could only trust its own optimistic state, which could diverge
  from what the server actually accepted (e.g., when function_manager filters
  out names whose plugins aren't loaded). After fix: response includes
  "functions" key with the accepted list so UI can re-sync from server truth.

  All three are static checks against the relevant source files. They will
  FAIL against pre-fix code (current state as of 2026-05-20) and PASS once
  the fixes land. Keep them as regression guards.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _strip_js_comments(src: str) -> str:
    """Strip // line comments and /* */ block comments from JS source.
    The regression check needs to look at CODE only — future comments
    can legitimately reference removed function names while explaining
    history (and one of ours does)."""
    src = re.sub(r'/\*[\s\S]*?\*/', '', src)
    src = re.sub(r'//[^\n]*', '', src)
    return src


def test_toolsets_js_debouncedSave_does_not_double_post():
    """ROOT CAUSE #1 — toolsets.js double-write.

    The `if (isActive) { await enableFunctions(...) }` block inside
    debouncedSave is the second POST that corrupted state. It must be
    removed — the server-side reapply_if_active triggered by the first
    POST is sufficient.
    """
    src = (ROOT / "interfaces/web/static/views/toolsets.js").read_text(encoding="utf-8")

    # Locate debouncedSave function body
    m = re.search(
        r'function\s+debouncedSave\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\n',
        src,
    )
    assert m, "Couldn't find debouncedSave function in toolsets.js"
    body = _strip_js_comments(m.group(1))

    # Before fix: code contains `await enableFunctions(...)` as an active call
    assert "enableFunctions(" not in body, (
        "debouncedSave still calls enableFunctions — the redundant second "
        "POST that corrupted current_toolset_name to 'custom' on every "
        "edit of the active toolset. The /api/toolsets/custom POST's "
        "server-side reapply_if_active is sufficient. Remove the "
        "`if (isActive) { await enableFunctions(...) }` block. "
        "(This check ignores // and /* */ comments, so a comment "
        "explaining the historical bug is fine — actual code calls are not.)"
    )


def test_sapphire_post_scan_reapply_unguarded_on_none_state():
    """ROOT CAUSE #2 — boot reapply guard blocks recovery.

    sapphire.py's post-scan toolset reapply runs `update_enabled_functions`
    only when `current != "none"`. When initial apply hit the dangling
    branch (toolset referenced plugin tools not yet loaded), state IS
    "none" — and the guard skips recovery. Remove the guard so the
    reapply runs unconditionally; calling with "none" is a safe no-op,
    calling with a previously-dangling name now resolves correctly.
    """
    src = (ROOT / "sapphire.py").read_text(encoding="utf-8")

    # Find the post-scan reapply block — anchored to the comment. The call's
    # ARGUMENTS are not this test's business (it also carries extra_toolsets
    # since 2026-08-05, the extras-decay fix); only the absence of the
    # `!= "none"` guard is.
    m = re.search(
        r'#\s*Re-apply toolset now that plugin tools are registered[\s\S]+?'
        r'fm\.update_enabled_functions\(\[current\][^)]*\)',
        src,
    )
    assert m, "Couldn't find post-scan reapply block in sapphire.py"
    block = m.group(0)

    # Before fix: contains `if current and current != "none":` guard
    assert 'current != "none"' not in block and "current != 'none'" not in block, (
        "sapphire.py post-scan reapply still has the `current != 'none'` "
        "guard. When the initial toolset apply lands in 'none' state (because "
        "a saved toolset references plugin tools that haven't loaded yet), "
        "this guard blocks the recovery call. Change the guard to just "
        "`if current:` — reapply with 'none' is a safe no-op, reapply with "
        "the named toolset now correctly re-resolves with plugin tools "
        "registered."
    )


def test_save_custom_toolset_response_echoes_accepted_functions():
    """ROOT CAUSE #3 — server doesn't echo accepted list.

    POST /api/toolsets/custom currently returns {status, name}. UI can't
    verify whether the server actually accepted the same list it sent
    (server may filter names whose plugins aren't loaded — see
    function_manager.py:746-749). Without the canonical list in the
    response, UI trusts its own optimistic state, which can silently
    diverge.

    Fix: response should include a "functions" key carrying the canonical
    accepted list, matching the pattern PUT /api/chats/{name}/settings
    already uses (which returns toolset/functions/state_tools).
    """
    src = (ROOT / "core/routes/content.py").read_text(encoding="utf-8")

    # Find save_custom_toolset's return statement
    m = re.search(
        r'async def save_custom_toolset[\s\S]+?return\s*(\{[\s\S]+?\})',
        src,
    )
    assert m, "Couldn't find save_custom_toolset return statement"
    return_block = m.group(1)

    # Before fix: return is {"status": "success", "name": name} only
    has_functions_key = (
        '"functions"' in return_block or "'functions'" in return_block
    )
    assert has_functions_key, (
        "save_custom_toolset response should include a 'functions' key "
        f"carrying the canonical accepted function list. Currently returns:\n"
        f"{return_block}\n\n"
        "UI uses this to re-sync after save instead of trusting its own "
        "optimistic checkbox state, which can diverge from server-truth "
        "when function_manager filters out plugin tools whose plugins "
        "aren't currently loaded."
    )
