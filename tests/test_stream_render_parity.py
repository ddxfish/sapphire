"""The live stream renders a reply the way history does (2026-11-12).

Two seams kept it apart for two years: the stream put every paragraph in one
<p>, and the live tool_end carried the MODEL's copy of a tool result (markers
stripped), so pictures and widgets showed only after the history swap.
- shared/prose.js is the one paragraph rule; the node corpus streams every text
  in many chunkings and must land on history's paragraphs.
- core.ui_markers.for_browser is the live tool_end's copy: markers whole.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from core.ui_markers import for_browser

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "js" / "prose.test.mjs"
STATIC = ROOT / "interfaces" / "web" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_prose_corpus_passes():
    r = subprocess.run(["node", str(CORPUS)], capture_output=True, text=True, timeout=30, cwd=str(ROOT))
    if r.returncode != 0:
        pytest.fail(f"prose corpus failed:\n{r.stdout}\n{r.stderr}")
    assert "passed" in r.stdout


def test_for_browser_keeps_every_marker():
    text = ('<<IMG::tool:abc>>\n(image img:abc)\nA picture.\n'
            '<!--GALLERY:[{"id":"x"}]-->\n<!--FILES:{"a":1}-->')
    assert for_browser(text) == text            # short: untouched, order kept


def test_for_browser_caps_words_not_markers():
    text = '<<IMG::tool:abc>>\n' + 'w' * 50 + '\n<!--ASK:{"id":"q"}-->'
    out = for_browser(text, limit=10)
    assert '<<IMG::tool:abc>>' in out and '<!--ASK:{"id":"q"}-->' in out
    assert 'w' * 10 in out and 'w' * 11 not in out and out.endswith('...')


def test_a_marker_shaped_blob_is_capped_not_waved_through():
    # `[^>]+` matched across lines: `<<AB::` + 300K + `>>` counted as one marker
    assert len(for_browser('<<AB::' + 'x' * 300000 + '>>')) < 5000


def test_moved_markers_sit_one_per_line():
    # the browser's GALLERY/FILES readers take a marker to the end of its line
    text = 'w' * 60 + '\n<!--GALLERY:[{"id":"a"}]-->\n<!--FILES:{"a":1}-->'
    out = for_browser(text, limit=10)
    assert out.startswith('<!--GALLERY:[{"id":"a"}]-->\n<!--FILES:{"a":1}-->\n')


def test_for_browser_non_text():
    assert for_browser(None) == ''


def test_the_live_tool_end_sends_the_browser_copy():
    src = (ROOT / "core" / "chat" / "chat_streaming.py").read_text(encoding="utf-8")
    assert '"result": for_browser(result_str)' in src                       # native tool calls
    assert '"result": for_browser(text_tool_result or "")' in src           # text-based tool calls
    assert '"error": not tool_ok' in src and '"error": not text_tool_ok' in src   # the tool's own verdict, both paths
    assert 'clean_result[:500]' not in src and 'result[:500]' not in src


def test_one_paragraph_rule_everywhere():
    for f in ("ui-parsing.js", "ui-streaming.js", "chat.js", "audio.js"):
        s = (STATIC / f).read_text(encoding="utf-8")
        assert "shared/prose.js" in s, f
        assert "split(/\\n\\s*\\n/)" not in s, f"{f} splits paragraphs by hand"


def test_continue_seeds_from_the_last_block_the_renderer_drew():
    s = (STATIC / "chat.js").read_text(encoding="utf-8")
    assert "const lastBlock = lastSeg?.type === 'text' ? proseBlocks(lastSeg.text).at(-1) : null;" in s
    assert "lastBlock?.type === 'para' ? lastBlock.text : ''" in s
    assert "thinkSegments(shown)" in s and "parseFilesMarker(prose).text" in s   # seeded from what was SHOWN


def test_a_fence_split_across_chunks_is_held_not_eaten():
    # token-by-token streams drew every ``` fence as prose until the swap:
    # a trailing ` or `` waits for the next chunk, and is flushed at every exit
    s = (STATIC / "ui-streaming.js").read_text(encoding="utf-8")
    # ...and a think tag split across chunks (`<th` + `ink>`, `</thi` + `nk>`)
    assert "const heldTail = (s) => s.match(/(?:`{1,2}|~{1,2}|<\\/?[A-Za-z:_]{0,24})$/)?.[0].length || 0;" in s
    assert s.count("heldTail(") == 3                         # the prose, code and think branches
    assert s.count("flushHeld();") >= 4                      # finish, cancel, detach, tool start


def test_think_steps_count_per_message_like_history():
    s = (STATIC / "ui-streaming.js").read_text(encoding="utf-8")
    assert "const thinkCount = contentDiv.querySelectorAll('details.accordion-think').length;" in s
    assert "container.querySelectorAll('details summary')" not in s     # the whole chat: "Step 29"


def test_a_tool_call_ends_a_round_so_the_next_think_can_open():
    # history keeps each round as its own part; live, `…is:` + tool + `<think>` ran
    # together and rule 4 (shared/think.js) read the opener as a word
    s = (STATIC / "ui-streaming.js").read_text(encoding="utf-8")
    start_tool = s.split("const doStartTool")[1].split("\n};")[0]
    assert "streamContent += '\\n';" in start_tool


def test_a_tool_image_clone_sits_after_its_own_tool():
    s = (STATIC / "core" / "events.js").read_text(encoding="utf-8")
    assert "const tool = loadedImg.closest('details');" in s
    assert "if (!tool || loadedImg.dataset.inlineClone) return;" in s     # prose/event images are already inline
    assert "+at.nextElementSibling.dataset.cloneOrder < order" in s       # the tool's own order, not load order


def test_one_tool_result_builder():
    s = (STATIC / "ui-streaming.js").read_text(encoding="utf-8")
    assert "buildToolResult(" in s
    for gone in ("parseGalleryMarker", "parseFilesMarker", "parseAskMarker", "parseStageMarker", "processMarkdown"):
        assert gone not in s, f"ui-streaming.js renders {gone} by itself again"
