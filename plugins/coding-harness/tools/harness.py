# Coding Harness — plugin tool
"""
Coding Harness — Sapphire's local coding bench.
File CRUD + content search + shell commands, all rooted in a configurable
working directory (default user/sapphire-workspace). Sandbox on by default:
file paths and command cwd stay inside the working directory.
"""

import fnmatch
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f9f0'
AVAILABLE_FUNCTIONS = [
    'read_file', 'write_file', 'edit_file',
    'list_files', 'search_files', 'run_command',
]

# .absolute() not .resolve() — resolve() follows symlinks and would root a
# symlinked plugin outside the sapphire tree.
_ROOT = Path(__file__).absolute().parent.parent.parent.parent

DEFAULTS = {
    'working_dir': 'user/sapphire-workspace',
    'sandbox': True,
    'output_limit': 6000,
    'max_timeout': 300,
    'blacklist': "rm -rf /\n--no-preserve-root\nmkfs\ndd if=/dev\n:(){ :|:& };:\n> /dev/sda\nchmod -R 777 /\ninit 0\ninit 6\nrm -rf ~\nrm -rf $HOME\nrm -rf \"$HOME\"",
}

READ_WINDOW = 500            # default lines per read_file call
LIST_CAP = 500               # max entries shown by list_files
SEARCH_CAP = 50              # default max search_files results
SEARCH_FILE_MAX = 1_000_000  # search skips files bigger than this
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "read_file",
            "description": "Read a text file with line numbers. Paths are relative to your working directory. Long files are windowed; the output says how to read more.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start_line": {"type": "integer", "description": "First line to show (default 1)"},
                    "end_line": {"type": "integer", "description": "Last line to show (default start_line+499)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file. Parent folders are created automatically. Set append=true to add to the end instead of overwriting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Full file content (or text to append)"},
                    "append": {"type": "boolean", "description": "Append instead of overwrite (default false)"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. old_text must match exactly (same whitespace) and appear once — read_file first and copy it precisely. Set replace_all=true to replace every occurrence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_text": {"type": "string", "description": "Exact text to replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "list_files",
            "description": "List a directory (default: working directory). Give pattern like '**/*.py' to glob-match files recursively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list (default working directory)"},
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py' or '**/*.js'"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "search_files",
            "description": "Search file contents with a regex. Returns file:line matches with the matching line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex to search for"},
                    "path": {"type": "string", "description": "Directory to search (default working directory)"},
                    "glob": {"type": "string", "description": "Only search files matching this name pattern, e.g. '*.py'"},
                    "max_results": {"type": "integer", "description": "Max matches returned (default 50)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Fresh shell each call — cd doesn't persist, use the cwd param. Prefer $HOME over ~ (quoted ~ goes literal). Every process the command starts is terminated when the call returns — backgrounding with & cannot outlive the call. grep/diff exit 1 = no match, not an error. Long output keeps start + tail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "cwd": {"type": "string", "description": "Directory to run in (default working directory)"},
                    "timeout": {"type": "integer", "description": "Seconds (default 120)"},
                    "max_output": {"type": "integer", "description": "Override output char limit for this call"}
                },
                "required": ["command"]
            }
        }
    },
]


class HarnessError(Exception):
    """User-facing tool error — the message goes straight back to the model."""


def _setting(settings, key):
    val = settings.get(key) if isinstance(settings, dict) else None
    return DEFAULTS[key] if val is None or val == '' else val


def _workdir(settings):
    raw = str(_setting(settings, 'working_dir'))
    wd = Path(os.path.expanduser(raw))
    if not wd.is_absolute():
        wd = _ROOT / wd
    wd = Path(os.path.normpath(str(wd)))
    wd.mkdir(parents=True, exist_ok=True)
    return wd


def _resolve(settings, path_str=None):
    """Resolve a tool path against the working dir; enforce the sandbox.

    Returns (path, workdir). No path → the working dir itself.
    """
    wd = _workdir(settings)
    if not path_str:
        return wd, wd
    p = Path(os.path.expanduser(str(path_str)))
    if not p.is_absolute():
        p = wd / p
    p = Path(os.path.normpath(str(p)))
    if _setting(settings, 'sandbox'):
        if p != wd and wd not in p.parents:
            raise HarnessError(
                f"{path_str} is outside the working directory ({wd}). "
                f"Sandbox is on — use relative paths, or turn it off in "
                f"Settings > Plugins > Coding Harness.")
    return p, wd


def _rel(p, wd):
    try:
        return str(p.relative_to(wd)) or '.'
    except ValueError:
        return str(p)


def _size(n):
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f}MB"
    return f"{n / 1024 ** 3:.1f}GB"


def _truncate_tail(text, limit):
    """Keep the head and the tail — errors print at the end."""
    if len(text) <= limit:
        return text, ''
    head = min(500, limit // 4)
    cut = len(text) - limit
    return (text[:head] + f"\n[... {cut} chars truncated ...]\n" + text[-(limit - head):],
            f" (output truncated: {len(text)} chars total)")


def _read_file(args, settings):
    p, wd = _resolve(settings, args.get('path'))
    if p.is_dir():
        raise HarnessError(f"{_rel(p, wd)} is a directory — use list_files.")
    if not p.is_file():
        raise HarnessError(f"File not found: {_rel(p, wd)}")
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    total = len(lines)
    if total == 0:
        return f"{_rel(p, wd)} — 0 lines (empty)", True
    start = max(1, int(args.get('start_line') or 1))
    if start > total:
        raise HarnessError(f"{_rel(p, wd)} has {total} lines — start_line {start} is past the end.")
    end = min(max(start, int(args.get('end_line') or start + READ_WINDOW - 1)), total)
    limit = int(_setting(settings, 'output_limit'))
    out, shown_end, used = [], start, 0
    for i in range(start, end + 1):
        line = f"{i:5d}  {lines[i - 1]}"
        if used + len(line) + 1 > limit and out:
            break
        out.append(line)
        used += len(line) + 1
        shown_end = i
    header = f"{_rel(p, wd)} — {total} lines, showing {start}–{shown_end}"
    footer = f"\n[continue with start_line={shown_end + 1}]" if shown_end < total else ''
    return header + '\n' + '\n'.join(out) + footer, True


def _atomic_write(p, text):
    """Temp file + rename so a crash or full disk can't destroy the original.
    A plain open('w') truncates first — any failure after that leaves 0 bytes."""
    tmp = p.with_name(p.name + '.harness-tmp')
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if p.exists():
            os.chmod(tmp, p.stat().st_mode)
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def _write_file(args, settings):
    p, wd = _resolve(settings, args.get('path'))
    if p.is_dir():
        raise HarnessError(f"{_rel(p, wd)} is a directory.")
    content = args.get('content', '')
    if not isinstance(content, str):
        raise HarnessError(
            f"content must be a string, got {type(content).__name__} — "
            f"file left untouched. Serialize objects yourself (e.g. JSON text).")
    append = bool(args.get('append'))
    p.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with open(p, 'a', encoding='utf-8', newline='') as f:
            f.write(content)
    else:
        _atomic_write(p, content)
    n = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
    verb = 'Appended' if append else 'Wrote'
    return f"{verb} {n} lines ({len(content.encode('utf-8'))} bytes) to {_rel(p, wd)}", True


def _edit_file(args, settings):
    p, wd = _resolve(settings, args.get('path'))
    if not p.is_file():
        raise HarnessError(f"File not found: {_rel(p, wd)}")
    old = args.get('old_text', '')
    new = args.get('new_text', '')
    if not old:
        raise HarnessError("old_text is required.")
    if old == new:
        raise HarnessError("old_text and new_text are identical.")
    raw = p.read_bytes()
    if b'\0' in raw:
        raise HarnessError(
            f"{_rel(p, wd)} is a binary file (contains NUL bytes) — refusing "
            f"to edit; a text edit would corrupt it.")
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        raise HarnessError(
            f"{_rel(p, wd)} is not valid UTF-8 ({e}) — refusing to edit; "
            f"writing it back would corrupt the original encoding.")
    count = text.count(old)
    if count == 0 and '\r\n' in text and '\r' not in old:
        # CRLF file, LF-normalized old_text from the model — retry translated.
        old_crlf = old.replace('\n', '\r\n')
        if old_crlf in text:
            old, new = old_crlf, new.replace('\n', '\r\n')
            count = text.count(old)
    if count == 0:
        raise HarnessError(
            f"old_text not found in {_rel(p, wd)} — whitespace must match "
            f"exactly. read_file it and copy the text precisely.")
    replace_all = bool(args.get('replace_all'))
    if count > 1 and not replace_all:
        raise HarnessError(
            f"old_text appears {count} times in {_rel(p, wd)} — include "
            f"surrounding lines to make it unique, or set replace_all=true.")
    text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    _atomic_write(p, text)
    return f"Replaced {count if replace_all else 1} occurrence(s) in {_rel(p, wd)}", True


def _list_files(args, settings):
    p, wd = _resolve(settings, args.get('path'))
    if not p.is_dir():
        raise HarnessError(f"Not a directory: {_rel(p, wd)}")
    pattern = args.get('pattern')
    entries = sorted(p.glob(pattern)) if pattern else sorted(p.iterdir())
    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if not e.is_dir()]
    out = []
    for e in (dirs + files)[:LIST_CAP]:
        if e.is_dir():
            out.append(f"{_rel(e, wd)}/")
        else:
            try:
                sz = _size(e.stat().st_size)
            except OSError:
                sz = '?'
            out.append(f"{_rel(e, wd)}  {sz}")
    header = _rel(p, wd) + (f" — glob {pattern}" if pattern else '')
    if not out:
        return header + ('\n(no matches)' if pattern else '\n(empty)'), True
    note = f"\n[{len(entries) - LIST_CAP} more not shown]" if len(entries) > LIST_CAP else ''
    return header + '\n' + '\n'.join(out) + note, True


def _search_files(args, settings):
    p, wd = _resolve(settings, args.get('path'))
    if not p.is_dir():
        raise HarnessError(f"Not a directory: {_rel(p, wd)}")
    try:
        rx = re.compile(args.get('pattern', ''))
    except re.error as e:
        raise HarnessError(f"Bad regex: {e}")
    name_glob = args.get('glob')
    cap = int(args.get('max_results') or SEARCH_CAP)
    hits, scanned = [], 0
    for root, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if name_glob and not fnmatch.fnmatch(fn, name_glob):
                continue
            fp = Path(root) / fn
            try:
                if fp.stat().st_size > SEARCH_FILE_MAX:
                    continue
                with open(fp, 'rb') as f:
                    if b'\0' in f.read(1024):
                        continue
                text = fp.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{_rel(fp, wd)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= cap:
                        return ('\n'.join(hits) +
                                f"\n[capped at {cap} — narrow with glob or path]"), True
    if not hits:
        return f"No matches for /{args.get('pattern')}/ in {_rel(p, wd)} ({scanned} files searched)", True
    return '\n'.join(hits), True


# Memory ceiling for a single command's captured output. The display limit
# truncates for readability; this bounds RAM so `yes` / `cat /dev/zero` can't
# OOM the whole assistant before truncation ever runs.
_MAX_CAPTURE_FLOOR = 2_000_000


_SECRET_MARKERS = ('API_KEY', 'ACCESS_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')


def _scrubbed_env():
    """Child env minus credentials — command output lands in chat history,
    and SSH_AUTH_SOCK would let any ssh command authenticate as the user."""
    return {k: v for k, v in os.environ.items()
            if k != 'SSH_AUTH_SOCK'
            and not any(m in k for m in _SECRET_MARKERS)}


def _kill_tree(proc, graceful=False):
    """Kill the command AND every descendant, not just the direct shell child.
    subprocess only ever signals `/bin/sh -c`; a pipeline or backgrounded job
    would leave grandchildren running (holding ports/CPU) after a timeout.
    POSIX: signal the whole process group — start_new_session makes the child
    its own group leader, so pgid == proc.pid, and that stays valid even after
    the leader is reaped as long as any group member survives (a pgid can't be
    reused while the group exists). graceful=True sends SIGTERM so package
    managers can finish their transaction; Windows taskkill is always forced."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGTERM if graceful else signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# Code-level gate, deliberately NOT part of the editable blacklist: these
# commands permanently discard uncommitted work, and working trees on this
# machine hold uncommitted changes that must never be lost.
_GIT_GATE = [
    (re.compile(r'\bgit\b[^|&;]*\breset\b[^|&;]*--(hard|merge)\b'), 'git reset --hard/--merge'),
    (re.compile(r'\bgit\b[^|&;]*\bcheckout\b'), 'git checkout'),
    (re.compile(r'\bgit\b[^|&;]*\brestore\b'), 'git restore'),
    (re.compile(r'\bgit\b[^|&;]*\bswitch\b[^|&;]*(--discard-changes\b|\s-\w*f|--force\b)'), 'git switch --force'),
    (re.compile(r'\bgit\b[^|&;]*\bclean\b[^|&;]*(\s-\w*f|\s--force\b)'), 'git clean -f'),
    (re.compile(r'\bgit\b[^|&;]*\bstash\b[^|&;]*\b(drop|clear)\b'), 'git stash drop/clear'),
    (re.compile(r'\bgit\b[^|&;]*\bpush\b[^|&;]*(\s--force\b|\s-f\b)'), 'git push --force'),
    (re.compile(r'\bcrontab\b[^|&;]*\s-\w*r'), 'crontab -r'),
]

_GIT_GATE_MSG = (
    "Blocked ({what}): destructive git/cron commands are disabled at code level "
    "in this harness. They permanently discard uncommitted work, and working "
    "trees on this machine hold uncommitted changes that must never be lost — "
    "this is not the editable blacklist and cannot be turned off in settings. "
    "To undo your own changes, edit the files back with edit_file/write_file. "
    "To change branches, use 'git switch <branch>' (it refuses to overwrite "
    "dirty files).")

# Hard ceiling on bytes flowing through the reader. Head+tail are kept in a
# bounded ring (middle discarded), so a chatty build can't OOM us — but a
# firehose like `yes` shouldn't get to spin for the whole timeout either.
_RUNAWAY_CAP = 100_000_000


def _run_command(args, settings):
    command = args.get('command')
    if command is not None and not isinstance(command, str):
        raise HarnessError(
            f"command must be a single shell-command string, got "
            f"{type(command).__name__} — join arguments into one string.")
    if not command:
        raise HarnessError("command is required.")
    for rx, what in _GIT_GATE:
        if rx.search(command):
            logger.warning(f"Command blocked by git gate ({what}): {command!r}")
            return _GIT_GATE_MSG.format(what=what), False
    bl = _setting(settings, 'blacklist')
    patterns = [l.strip() for l in bl.split('\n') if l.strip()] if isinstance(bl, str) else [str(x) for x in (bl or [])]
    for pattern in patterns:
        if pattern in command:
            logger.warning(f"Command blocked by blacklist: {command!r} matched {pattern!r}")
            return (f"Command blocked by safety filter (matched: {pattern}). "
                    f"Edit the blacklist in Settings > Plugins > Coding Harness.", False)
    cwd, wd = _resolve(settings, args.get('cwd'))
    if not cwd.is_dir():
        raise HarnessError(f"cwd does not exist: {_rel(cwd, wd)}")
    max_timeout = max(5, int(_setting(settings, 'max_timeout')))
    timeout = min(max(5, int(args.get('timeout') or 120)), max_timeout)
    limit = int(args.get('max_output') or _setting(settings, 'output_limit'))
    limit = max(200, min(limit, 1_000_000))

    logger.info(f"HARNESS [{cwd}] $ {command[:100]}")
    # Own process group so a timeout can kill the whole tree (see _kill_tree),
    # and stderr merged into stdout so a single byte cap bounds RAM. Read in a
    # thread keeping a bounded head+tail ring; the main loop is the wall clock.
    capture_cap = max(limit * 4, _MAX_CAPTURE_FLOOR)
    popen_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    shell=True, cwd=str(cwd), env=_scrubbed_env())
    if os.name == 'nt':
        popen_kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw['start_new_session'] = True
    try:
        proc = subprocess.Popen(command, **popen_kw)
    except Exception as e:
        raise HarnessError(f"Failed to launch command: {e}")

    # Head + rolling tail, both bounded — a chatty pip/npm/pytest run keeps
    # draining (never blocks the child, never kills its transaction); only the
    # middle of oversized output is discarded. Errors print at the end, so the
    # tail is the part worth keeping.
    head_cap = capture_cap // 2
    head, tail = [], deque()
    state = {'head': 0, 'tail': 0, 'discarded': 0}

    def _drain():
        try:
            while True:
                b = proc.stdout.read(65536)
                if not b:
                    break
                if state['head'] < head_cap:
                    take = min(len(b), head_cap - state['head'])
                    head.append(b[:take])
                    state['head'] += take
                    b = b[take:]
                if b:
                    tail.append(b)
                    state['tail'] += len(b)
                    while state['tail'] > capture_cap - head_cap:
                        dropped = tail.popleft()
                        state['tail'] -= len(dropped)
                        state['discarded'] += len(dropped)
        except Exception:
            logger.warning(f"Output reader died for: {command[:100]}", exc_info=True)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    end = time.monotonic() + timeout
    timed_out = runaway = False
    while True:
        try:
            proc.wait(timeout=0.2)
            break                        # exited on its own
        except subprocess.TimeoutExpired:
            if state['discarded'] > _RUNAWAY_CAP:
                runaway = True
                break
            if time.monotonic() >= end:
                timed_out = True
                break
    if proc.poll() is None:
        _kill_tree(proc, graceful=True)  # SIGTERM first — let pip/npm land their transaction
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    # Reap the group UNCONDITIONALLY, even after a clean exit: `cmd &` returns
    # rc=0 immediately but its orphan keeps the pipe write-end open — left
    # alive, the blocked reader wedges stdout.close() below (BufferedReader
    # lock) and the calling worker thread with it, forever.
    _kill_tree(proc)
    if proc.poll() is None:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    reader.join(timeout=2)
    if reader.is_alive():
        # A setsid/systemd-run escapee we can't kill still holds the pipe and
        # the reader holds the buffer lock — closing here would block forever.
        # Leak the fd (freed at GC when the escapee exits) instead of wedging.
        logger.warning(f"Unkillable pipe holder survived (setsid child?): {command[:100]}")
    else:
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _dec(b):
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError:
            # Windows console tools emit the OEM codepage (cp850/cp437), not
            # UTF-8 — the 'oem' codec keeps their output readable.
            return b.decode('oem' if os.name == 'nt' else 'utf-8', 'replace')

    full = _dec(b''.join(head))
    if state['discarded']:
        full += f"\n[... {_size(state['discarded'])} of output discarded (middle) ...]\n"
    if tail:
        full += _dec(b''.join(tail))
    full = full or '(no output)'
    full, note = _truncate_tail(full, limit)
    if state['discarded']:
        note += f" ({_size(state['discarded'])} of middle output discarded)"
    if timed_out:
        extra, ok = f" — TIMED OUT after {timeout}s (process tree killed)", False
        logger.warning(f"Command timed out after {timeout}s: {command[:100]}")
    elif runaway:
        extra, ok = f" — RUNAWAY OUTPUT (>{_size(_RUNAWAY_CAP)} discarded, process tree killed)", False
        logger.warning(f"Command produced runaway output: {command[:100]}")
    else:
        extra, ok = "", proc.returncode == 0
    header = f"[{_rel(cwd, wd)}] $ {command}\nExit code: {proc.returncode}{extra}{note}"
    return f"{header}\n\n{full}", ok


_HANDLERS = {
    'read_file': _read_file,
    'write_file': _write_file,
    'edit_file': _edit_file,
    'list_files': _list_files,
    'search_files': _search_files,
    'run_command': _run_command,
}


def execute(function_name, arguments, config, plugin_settings=None):
    try:
        handler = _HANDLERS.get(function_name)
        if not handler:
            return f"Unknown function '{function_name}'.", False
        return handler(arguments or {}, plugin_settings or {})
    except HarnessError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"Coding harness error: {e}", exc_info=True)
        return f"Error: {e}", False
