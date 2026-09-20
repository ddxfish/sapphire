"""shared/settings-commit.js corpus, run under node (same pattern as
test_dom_guard_js.py). U1 (b), 2026-09-20: the Settings view's write-through
primitives. Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "settings-commit.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_settings_commit_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"settings-commit corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
