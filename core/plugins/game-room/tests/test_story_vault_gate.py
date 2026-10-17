# Story × vault gate (vault recon finding 7, phase 7 of tmp/prompt-vault-plan.md).
#
# local/combined identity modes extract the LOCAL prompt's character text and
# persist the rendered costume to a PLAINTEXT sidecar that re-merges every
# boot. A vault prompt's decrypted text must never take that ride — the gate
# lives in _register_prompt so all five callers (start/resume/revert and
# _set_mode re-pointing a RUNNING story) are covered by one check.
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import session as sess  # noqa: E402


class _Reached(Exception):
    """Sentinel: execution got past the gate into the render machinery."""


@pytest.fixture
def harness(monkeypatch):
    """Stop _register_prompt right after the gate — these tests only care
    whether the gate fired, not what the renderer does."""
    import core.prompt_crud as pc
    monkeypatch.setattr(sess, "_manifest_prompts", lambda: ({}, {}))
    monkeypatch.setattr(sess, "_local_ctx",
                        lambda name: (_ for _ in ()).throw(_Reached()))
    monkeypatch.setattr(pc, "is_vault_prompt", lambda n: n == "vlt_moonlight")
    return {"meta": {"slug": "gatetest"}}


def test_local_mode_refuses_vault_prompt(harness):
    with pytest.raises(ValueError, match="vault"):
        sess._register_prompt(harness, {}, {"mode": "local",
                                            "local": "vlt_moonlight"}, "chat1")


def test_combined_mode_refuses_vault_prompt(harness):
    with pytest.raises(ValueError, match="vault"):
        sess._register_prompt(harness, {}, {"mode": "combined",
                                            "local": "vlt_moonlight"}, "chat1")


def test_local_mode_with_regular_prompt_passes_gate(harness):
    """Non-vault local prompt → the gate lets it through to the renderer
    (the _Reached sentinel proves we got past the check)."""
    with pytest.raises(_Reached):
        sess._register_prompt(harness, {}, {"mode": "local",
                                            "local": "plain-user-prompt"}, "chat1")


def test_story_mode_ignores_vault_state(harness, monkeypatch):
    """Pure 'story' mode carries no local text — allowed regardless. With no
    entry['local'] the _local_ctx sentinel never fires; stop at render."""
    monkeypatch.setattr(sess.render, "story_prompt",
                        lambda *a, **k: (_ for _ in ()).throw(_Reached()))
    with pytest.raises(_Reached):
        sess._register_prompt(harness, {}, {"mode": "story"}, "chat1")
