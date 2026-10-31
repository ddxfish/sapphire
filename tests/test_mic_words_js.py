"""shared/mic-words.js corpus, run under node (same pattern as
test_notify_js.py): the words for each refusal, the chosen mic. Skips cleanly
without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "tests" / "js" / "mic-words.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_mic_words_corpus_passes():
    result = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True,
                            timeout=30, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        pytest.fail(f"mic-words corpus failed (exit {result.returncode}):\n"
                    f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert "passed" in result.stdout, result.stdout
