"""shared/think.js is the twin of core/think.py: same names, same cases, same answer.

The web UI cannot import Python, so the rules live twice. This test is what
keeps them from drifting: node runs the JS reader on every case below and the
result must equal core.think.segments. Skipped where node is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import think

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'interfaces/web/static/shared/think.js'

CASES = [
    '',
    'Just a plain answer.',
    '<think>Let me reason.</think>The answer is 42.',
    '<think>\nInternal reasoning here.\n</think>\nHello world',
    '<seed:think>Reasoning here</seed:think>Visible answer.',
    '<thinking>plan</thinking>Visible reply',
    '<redacted_thinking>x</redacted_thinking>ok',
    '<reasoning>why</reasoning>Because.',
    '<THINK>loud</THINK>quiet',
    '<think type="deep">x</think >Answer',
    '<thinker>not a tag</thinker>',
    '<think>First thought</think>Middle text <think>Second thought</think>Final answer',
    '<seed:think>plan<seed:cot_budget_reflect>used 40</seed:cot_budget_reflect>more plan</seed:think>Hi',
    'Still thinking...</think>Here is the real answer.',
    '<think>A</think>B</think>C',
    'Answer one. <think>a</think>Answer two. <think>b</think>stray</think>Answer three.',
    '<think>cut off mid-thought',
    '  <think>never closes and keeps going',
    "Some answer.<think>I'm still working on this",
    'Done.\n<think>more',
    'You can wrap reasoning in <think> tags like this. Models do it a lot.',
    'Reasoning models emit `<think>` and `</think>` around their thoughts.',
    ('<think>The image came with two things:\nA `<think>` block and a description. '
     'It even has a `</think>` in it.</think>\n\nYes, I can see the full caption.'),
    'The format is:\n```\n<think>reasoning</think>\n```\nThat is all.',
    '<think>plan</think>Format:\n```xml\n<think>x</think>\n```\nDone.',
    '<think>I will write:\n```python\nprint(1)\n```\nok</think>Here it is.',
    '<think>Only thinking here</think>',
    'Hi <seed:think>a</seed:think>there <think>b</think>end',
    '~~~\n</think>\n~~~\nplain fence',
    '<think>   </think>empty block',
]

RUNNER = """
import { thinkSegments, OPEN_NAMES, CLOSE_NAMES } from './think.mjs';
import { readFileSync } from 'node:fs';
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify({
    open: OPEN_NAMES, close: CLOSE_NAMES,
    segments: cases.map(c => thinkSegments(c).map(s => [s.type, s.text, s.name])),
}));
"""


@pytest.fixture(scope='module')
def twin(tmp_path_factory):
    if shutil.which('node') is None:
        pytest.skip('node is not installed')
    box = tmp_path_factory.mktemp('think_twin')
    (box / 'think.mjs').write_text(JS.read_text(encoding='utf-8'), encoding='utf-8')
    (box / 'run.mjs').write_text(RUNNER, encoding='utf-8')
    (box / 'cases.json').write_text(json.dumps(CASES), encoding='utf-8')
    done = subprocess.run(['node', str(box / 'run.mjs'), str(box / 'cases.json')],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_twin_knows_the_same_tag_names(twin):
    assert tuple(twin['open']) == think.OPEN_NAMES
    assert tuple(twin['close']) == think.CLOSE_NAMES


def test_the_twin_reads_every_case_the_same(twin):
    assert len(twin['segments']) == len(CASES)
    for case, got in zip(CASES, twin['segments']):
        want = [list(seg) for seg in think.segments(case)]
        assert got == want, f'the twins disagree on: {case!r}'


def test_no_other_reader_is_left_in_the_web_ui():
    """Every strip and split in the web UI goes through shared/think.js."""
    static = ROOT / 'interfaces/web/static'
    for path in static.rglob('*.js'):
        if path.name == 'think.js':
            continue
        src = path.read_text(encoding='utf-8', errors='replace')
        assert 'seed:cot_budget_reflect' not in src, f'{path.relative_to(ROOT)} still carries its own think-tag pattern'


def test_no_other_reader_is_left_in_python():
    """core/think.py is the one module that spells a think tag in a pattern."""
    for top in ('core', 'plugins', 'functions'):
        for path in (ROOT / top).rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if rel == 'core/think.py' or '/tests/' in rel:
                continue
            src = path.read_text(encoding='utf-8', errors='replace')
            assert 'seed:cot_budget_reflect' not in src, f'{rel} still carries its own think-tag pattern'
            assert "r'<think>" not in src and 'r"<think>' not in src, f'{rel} still carries its own think-tag pattern'
