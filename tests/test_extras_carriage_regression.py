"""extra_toolsets carriage — static regression guard (post-fix review 2026-08-05).

update_enabled_functions() REPLACES the enabled set, so any by-name re-apply
that omits `extra_toolsets=` silently strips the chat's extras union — the
"include story tools" checkbox — and mid-story she loses story_act: she
narrates, nothing advances, and settings still claim the extras are on.

This bug class recurred TEN times across three days (extras-decay sites
#1-#10, 2026-08-03 → 2026-08-05) because the runtime tests mock the function
manager and accept any call signature. Static guard, same style as
test_toolset_state_corruption_regression.py: every call site in shipped code
must pass `extra_toolsets=` or be allowlisted below WITH a reason.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN = ["sapphire.py", "core", "functions", "plugins"]

# (repo-relative path, snippet that must appear inside the bare call, reason)
ALLOWED_BARE = [
    # Isolated task context (phone/agent one-shots): scopes are deliberately
    # reset fresh and the restore leg at the end of the isolated call DOES
    # carry the original extras back.
    ("core/chat/chat.py", "[toolset]"),
]


def _calls(text):
    """Every update_enabled_functions(...) call with balanced parens."""
    for m in re.finditer(r"update_enabled_functions\s*\(", text):
        depth, j = 1, m.end()
        while j < len(text) and depth:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        yield text[m.start():j]


def test_every_toolset_reapply_carries_extra_toolsets():
    files = []
    for entry in SCAN:
        p = ROOT / entry
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.py"))

    bare = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if "/tests/" in rel or rel.startswith("tests/"):
            continue
        if rel == "core/chat/function_manager.py":
            continue  # the definition + its internal dangling-toolset reset
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for call in _calls(text):
            if "extra_toolsets" in call:
                continue
            if any(rel == arel and snip in call for arel, snip in ALLOWED_BARE):
                continue
            bare.append(f"{rel}: {' '.join(call.split())[:100]}")

    assert not bare, (
        "update_enabled_functions call(s) missing extra_toolsets= — a by-name "
        "re-apply without it strips the chat's extras union (story tools). "
        "Carry the chat's extras or allowlist here with a reason:\n  "
        + "\n  ".join(bare)
    )
