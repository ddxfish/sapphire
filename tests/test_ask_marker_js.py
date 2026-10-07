"""shared/ask-marker.js corpus, run under node (same pattern as
test_files_marker_js.py). The ONE question-card renderer: marker parse + strip,
the card's click-through, the answer text, locking. 2026-10-07.
Skips cleanly without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "ask-marker.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ask_marker_corpus_passes():
    assert CORPUS.exists(), CORPUS
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"ask-marker corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout.lower(), result.stdout
