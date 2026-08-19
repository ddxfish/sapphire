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


def test_sapphire_post_scan_apply_reads_chat_settings_not_fm_state():
    """ROOT CAUSE #2 — boot toolset apply must come from CHAT SETTINGS.

    History: the 2026-05-16/20 post-scan block captured
    fm.current_toolset_name and re-applied it. That healed the common case
    (name resolved early, plugin tools filtered out — current kept the
    name) but NOT the dangling case: an unresolvable name at the early
    apply rewrites current to 'none', and re-applying 'none' cements zero
    tools until a human toggles the chat's toolset — while the UI still
    shows the stored setting (Prime 'Enabled: []', 2026-08-19).

    Contract now: the pre-scan apply STRIPS the toolset keys entirely
    (plugin tools don't exist yet — same dependency the prompt leg has),
    and the post-scan apply resolves the toolset from the chat's STORED
    settings (intent), never from fm state (a runtime echo that may
    already be the fallback).
    """
    src = (ROOT / "sapphire.py").read_text(encoding="utf-8")

    # Post-scan block: anchored to its comment, must read get_chat_settings
    # and must NOT re-apply a captured fm.current_toolset_name.
    m = re.search(
        r'#\s*Apply the toolset now that plugin tools are registered'
        r'[\s\S]+?update_enabled_functions\([^)]*\)',
        src,
    )
    assert m, "Couldn't find post-scan toolset apply block in sapphire.py"
    block = m.group(0)
    assert "get_chat_settings" in block, (
        "sapphire.py post-scan toolset apply no longer reads the chat's "
        "stored settings. It must resolve the toolset name from "
        "get_chat_settings() — fm.current_toolset_name is a runtime echo "
        "that reads 'none' after a dangling fallback, and re-applying it "
        "cements zero tools until a human toggles the chat's toolset."
    )
    assert "update_enabled_functions([current]" not in block, (
        "sapphire.py post-scan toolset apply re-applies a captured "
        "fm.current_toolset_name again. That value is 'none' whenever the "
        "early apply hit the dangling fallback — re-applying it cements "
        "zero enabled tools (Prime 'Enabled: []', 2026-08-19). Apply from "
        "the chat's stored settings instead."
    )

    # Pre-scan apply: _apply_initial_chat_settings must strip toolset keys
    # so the only boot toolset apply happens post-scan.
    m2 = re.search(
        r'def _apply_initial_chat_settings\(self\):[\s\S]+?'
        r'_apply_chat_settings\([^)]*\)',
        src,
    )
    assert m2, "Couldn't find _apply_initial_chat_settings in sapphire.py"
    body = m2.group(0)
    assert '"toolset"' in body and '"ability"' in body, (
        "_apply_initial_chat_settings no longer strips the toolset/ability "
        "keys. Applying the toolset before plugin_loader.scan() drops every "
        "plugin-provided function ('references N unavailable function(s)' "
        "each boot) and can land in the dangling fallback. The post-scan "
        "apply owns the toolset leg."
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
