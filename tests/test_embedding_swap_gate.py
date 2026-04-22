"""Regression guard for 2026-04-22 sapphire-killer fix C.

Pre-fix: the swap gate at core/routes/settings.py:207-210 caught Exception
and fell through to the normal swap path. If integrity_report() raised (DB
busy with re-embed, VACUUM, or heavy search — >10s lock timeout → sqlite3
OperationalError), the gate was bypassed and the user got a silent
destructive swap they would have refused if the 409 warning had rendered.

Fix: fail CLOSED. Raise 503 with a specific error body explaining the
user's options (wait for busy op, OR explicit confirm flag).
"""
import inspect


def test_swap_gate_fails_closed_on_integrity_exception():
    """Source-level guard — the gate must RAISE on integrity_report failure,
    not silently fall through.

    We inspect the route source for the anti-pattern. Writing a full HTTP-
    level test would require standing up the whole app + mocking settings +
    mocking integrity_report — complex for what is ultimately a 15-line
    source-discipline guard.
    """
    from pathlib import Path
    source = (Path(__file__).parent.parent / "core" / "routes" / "settings.py").read_text()

    # Find the swap-gate try/except region
    idx = source.find("EMBEDDING_PROVIDER' in settings_dict")
    assert idx != -1, "Swap gate block not found — has the route been restructured?"

    # Window of ~2000 chars around that region
    window = source[idx:idx + 3000]

    # Must have a raise HTTPException with status_code=503 inside the except block
    assert "status_code=503" in window, \
        "Swap gate must raise 503 when integrity check fails (fail-CLOSED, not fall-through)"
    assert "embedding_swap_gate_unavailable" in window, \
        "503 response body must use the 'embedding_swap_gate_unavailable' error key"

    # Must NOT have the old fall-through pattern
    forbidden = [
        "fall through — we don't want to break",
        "# If integrity check fails, fall through",
    ]
    for s in forbidden:
        assert s not in window, \
            f"Forbidden fall-through comment still present: {s!r}"


def test_swap_gate_preserves_confirm_bypass():
    """User with `confirm_embedding_swap: true` should still bypass the
    integrity check entirely — that's the escape hatch for users who
    accept the swap risk knowingly."""
    from pathlib import Path
    source = (Path(__file__).parent.parent / "core" / "routes" / "settings.py").read_text()

    # The confirm-check short-circuit must be BEFORE the integrity_report call
    # — if it runs after, the 503 would fire even for users who confirmed.
    idx_confirm = source.find("data.get('confirm_embedding_swap')")
    idx_integ = source.find("integrity_report")
    assert idx_confirm != -1, "confirm_embedding_swap check must exist"
    assert idx_integ != -1, "integrity_report call must exist"
    assert idx_confirm < idx_integ, \
        "confirm_embedding_swap check must run BEFORE integrity_report — " \
        "users who explicitly confirm must not be blocked by a 503"


def test_swap_gate_message_has_recovery_guidance():
    """The 503 message should tell users WHAT TO DO to proceed — not just fail."""
    from pathlib import Path
    source = (Path(__file__).parent.parent / "core" / "routes" / "settings.py").read_text()

    idx = source.find("embedding_swap_gate_unavailable")
    assert idx != -1
    window = source[idx:idx + 1500]

    # Should mention the bypass mechanism
    assert "confirm_embedding_swap" in window, \
        "503 message should tell user about confirm_embedding_swap bypass"
    # Should mention why it happens (busy DB) so user can wait instead
    assert "busy" in window.lower(), \
        "503 message should explain DB-busy cause"
