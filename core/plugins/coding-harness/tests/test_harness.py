# Tests for the coding-harness tools — covers the 2026-08-09 bug-hunt batch:
# A (backgrounded-proc wedge), B (blacklist defaults), C (array command bypass),
# D (non-str write truncation), E (atomic writes), F (binary/encoding guard),
# G (CRLF preservation), M/O/P (settings clamps), git gate.
import importlib.util
import os
import time
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).parent.parent / 'tools' / 'harness.py'
_spec = importlib.util.spec_from_file_location('coding_harness_under_test', _HARNESS_PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


@pytest.fixture
def settings(tmp_path):
    return {
        'working_dir': str(tmp_path),
        'sandbox': True,
        'output_limit': 6000,
        'max_timeout': 300,
        'blacklist': '',  # '' falls through to module DEFAULTS
    }


def run(name, args, settings):
    return harness.execute(name, args, {}, settings)


# ── C: array/dict command must be rejected, not shell-executed ──

def test_array_command_rejected(settings):
    out, ok = run('run_command', {'command': ['rm -rf ~', 'x']}, settings)
    assert not ok
    assert 'single shell-command string' in out

def test_dict_command_rejected(settings):
    out, ok = run('run_command', {'command': {'cmd': 'echo hi'}}, settings)
    assert not ok
    assert 'single shell-command string' in out


# ── B: blacklist defaults include the home-nuke forms ──

@pytest.mark.parametrize('cmd', ['rm -rf ~', 'rm -rf $HOME/x', 'rm -rf "$HOME"'])
def test_blacklist_blocks_home_nuke(cmd, settings):
    out, ok = run('run_command', {'command': cmd}, settings)
    assert not ok
    assert 'blocked by safety filter' in out


# ── git gate: destructive verbs blocked with an explanation ──

@pytest.mark.parametrize('cmd', [
    'git reset --hard HEAD',
    'git checkout -- .',
    'git checkout main',
    'git restore .',
    'git clean -fdx',
    'git clean --force',
    'git stash drop',
    'git stash clear',
    'git push --force origin main',
    'git push -f',
    'git switch --discard-changes main',
    'crontab -r',
])
def test_git_gate_blocks(cmd, settings):
    out, ok = run('run_command', {'command': cmd}, settings)
    assert not ok
    assert 'edit the files back' in out  # the "why" reaches the model

@pytest.mark.parametrize('cmd', [
    'git status', 'git diff', 'git log --oneline', 'git add .',
    'git commit -m "x"', 'git switch main', 'git stash', 'crontab -l',
])
def test_git_gate_allows_safe(cmd, settings):
    out, ok = run('run_command', {'command': cmd}, settings)
    # Runs in an empty tmp dir so git itself fails — the point is the gate
    # didn't intercept it.
    assert 'edit the files back' not in out
    assert 'safety filter' not in out


# ── A: backgrounded process can't wedge the call ──

def test_backgrounded_proc_returns_fast(settings):
    t0 = time.monotonic()
    out, ok = run('run_command', {'command': 'sleep 10 &'}, settings)
    elapsed = time.monotonic() - t0
    assert elapsed < 5, f"call wedged for {elapsed:.1f}s on a backgrounded child"
    assert 'Exit code: 0' in out


# ── D: non-str content must not truncate the file ──

def test_write_file_non_str_content_preserves_file(settings, tmp_path):
    f = tmp_path / 'precious.txt'
    f.write_text('26 bytes of original data\n')
    out, ok = run('write_file', {'path': 'precious.txt', 'content': {'a': 1}}, settings)
    assert not ok
    assert 'must be a string' in out
    assert f.read_text() == '26 bytes of original data\n'


# ── E: atomic write basics ──

def test_write_leaves_no_tmp_file(settings, tmp_path):
    out, ok = run('write_file', {'path': 'out.txt', 'content': 'hello\n'}, settings)
    assert ok
    assert (tmp_path / 'out.txt').read_text() == 'hello\n'
    assert not list(tmp_path.glob('*.harness-tmp'))

def test_append_still_appends(settings, tmp_path):
    (tmp_path / 'log.txt').write_text('one\n')
    out, ok = run('write_file', {'path': 'log.txt', 'content': 'two\n', 'append': True}, settings)
    assert ok
    assert (tmp_path / 'log.txt').read_text() == 'one\ntwo\n'


# ── F: binary / non-UTF-8 files refuse edits instead of corrupting ──

def test_edit_refuses_binary(settings, tmp_path):
    (tmp_path / 'blob.bin').write_bytes(b'ab\x00cd')
    out, ok = run('edit_file', {'path': 'blob.bin', 'old_text': 'ab', 'new_text': 'xy'}, settings)
    assert not ok
    assert 'binary' in out
    assert (tmp_path / 'blob.bin').read_bytes() == b'ab\x00cd'

def test_edit_refuses_latin1(settings, tmp_path):
    raw = 'café résumé\n'.encode('latin-1')
    (tmp_path / 'legacy.txt').write_bytes(raw)
    out, ok = run('edit_file', {'path': 'legacy.txt', 'old_text': 'caf', 'new_text': 'CAF'}, settings)
    assert not ok
    assert 'not valid UTF-8' in out
    assert (tmp_path / 'legacy.txt').read_bytes() == raw


# ── G: CRLF files keep their line endings through an edit ──

def test_edit_preserves_crlf(settings, tmp_path):
    (tmp_path / 'win.txt').write_bytes(b'line1\r\nline2\r\nline3\r\n')
    out, ok = run('edit_file', {'path': 'win.txt', 'old_text': 'line2', 'new_text': 'LINE2'}, settings)
    assert ok
    assert (tmp_path / 'win.txt').read_bytes() == b'line1\r\nLINE2\r\nline3\r\n'

def test_edit_crlf_with_lf_old_text(settings, tmp_path):
    # Model supplies LF-normalized old_text spanning lines of a CRLF file.
    (tmp_path / 'win.txt').write_bytes(b'aa\r\nbb\r\ncc\r\n')
    out, ok = run('edit_file', {'path': 'win.txt', 'old_text': 'bb\ncc', 'new_text': 'dd\nee'}, settings)
    assert ok
    assert (tmp_path / 'win.txt').read_bytes() == b'aa\r\ndd\r\nee\r\n'


# ── L: big output no longer kills the process or fails the call ──

def test_big_output_survives_with_middle_discarded(settings):
    cmd = "python3 -c \"import sys; sys.stdout.write('x'*3_000_000); sys.stdout.write('TAIL_MARKER')\""
    out, ok = run('run_command', {'command': cmd, 'timeout': 60}, settings)
    assert ok, f"big output should not fail the call: {out[:200]}"
    assert 'middle output discarded' in out
    assert 'Exit code: 0' in out


# ── M/O/P: hostile settings clamped ──

def test_output_limit_zero_does_not_invert(settings):
    settings['output_limit'] = 0
    out, ok = run('run_command', {'command': 'echo clamped'}, settings)
    assert ok
    assert 'clamped' in out

def test_max_timeout_zero_still_runs(settings):
    settings['max_timeout'] = 0
    out, ok = run('run_command', {'command': 'echo alive'}, settings)
    assert ok
    assert 'alive' in out


# ── sanity: the six tools still work end-to-end ──

def test_roundtrip(settings, tmp_path):
    _, ok = run('write_file', {'path': 'a/b.txt', 'content': 'needle here\n'}, settings)
    assert ok
    out, ok = run('read_file', {'path': 'a/b.txt'}, settings)
    assert ok and 'needle here' in out
    out, ok = run('list_files', {'path': 'a'}, settings)
    assert ok and 'b.txt' in out
    out, ok = run('search_files', {'pattern': 'needle', 'path': 'a'}, settings)
    assert ok and 'b.txt:1' in out
    out, ok = run('edit_file', {'path': 'a/b.txt', 'old_text': 'needle', 'new_text': 'thread'}, settings)
    assert ok
    out, ok = run('run_command', {'command': 'cat a/b.txt'}, settings)
    assert ok and 'thread here' in out
