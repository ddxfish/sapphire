"""shared/dom-guard.js corpus, run under node (same pattern as
test_checklist_modal_js.py). The four moves every surgical refresh reaches
for — editable check, deferred soft refresh (+ busy / kick), scroll carry,
focus carry — and the focus policy. DOM-refresh hunt, 2026-09-08. Skips
cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "dom-guard.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dom_guard_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"dom-guard corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
