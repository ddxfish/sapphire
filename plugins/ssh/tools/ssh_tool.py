# SSH tool — plugin tool
"""
SSH tool — AI can list servers and run commands on remote machines.
Uses system `ssh` via subprocess. Servers configured in Settings > Plugins > SSH.
Commands checked against a configurable blacklist before execution.
"""

import os
import subprocess
import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f5a5\ufe0f'
TOOL_CATEGORY = 'system'
AVAILABLE_FUNCTIONS = [
    'ssh_get_servers',
    'ssh_run_command',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "ssh_get_servers",
            "description": "List configured SSH servers, or one's details by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": True,
        "function": {
            "name": "ssh_run_command",
            "description": "Run a shell command on a remote server. Long output is truncated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "From ssh_get_servers"
                    },
                    "command": {
                        "type": "string"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds (default 30)"
                    }
                },
                "required": ["server", "command"]
            }
        }
    }
]

# Default blacklist — dangerous commands blocked by default
DEFAULT_BLACKLIST = [
    "rm -rf /",
    "rm -rf /*",
    "--no-preserve-root",
    "mkfs",
    "dd if=/dev",
    ":(){ :|:& };:",
    "> /dev/sda",
    "chmod -R 777 /",
    "init 0",
    "init 6",
]

DEFAULT_OUTPUT_LIMIT = 6000
DEFAULT_MAX_TIMEOUT = 120


# ─── Settings Access ─────────────────────────────────────────────────────────

def _get_ssh_settings():
    """Load SSH plugin settings (output_limit, max_timeout, blacklist)."""
    settings_file = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "ssh.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _get_blacklist():
    """Get the command blacklist (user-configured or defaults)."""
    settings = _get_ssh_settings()
    bl = settings.get('blacklist')
    if bl is not None:
        # Could be a string (textarea) or list
        if isinstance(bl, str):
            return [line.strip() for line in bl.split('\n') if line.strip()]
        return bl
    return DEFAULT_BLACKLIST


def _get_output_limit():
    settings = _get_ssh_settings()
    return settings.get('output_limit', DEFAULT_OUTPUT_LIMIT)


def _get_max_timeout():
    settings = _get_ssh_settings()
    return settings.get('max_timeout', DEFAULT_MAX_TIMEOUT)


def _check_blacklist(command):
    """Check command against blacklist. Returns matching pattern or None."""
    blacklist = _get_blacklist()
    for pattern in blacklist:
        if not pattern:
            continue
        try:
            if re.search(pattern, command):
                return pattern
        except re.error:
            # Invalid regex — fall back to substring match
            if pattern in command:
                return pattern
    return None


# ─── Tool Implementations ────────────────────────────────────────────────────

def _get_servers(name=None):
    from core.credentials_manager import credentials
    all_servers = credentials.get_ssh_servers()
    active = [s for s in all_servers if s.get('enabled', True)]
    active_names = [s['name'] for s in active]

    if not active:
        return "No SSH servers available. Add or enable servers in Settings > Plugins > SSH.", True

    if name:
        server = next((s for s in active if s['name'].lower() == name.lower()), None)
        if not server:
            return f"Server '{name}' not found. Available: {', '.join(active_names)}", False
        return (
            f"Server: {server['name']}\n"
            f"  Host: {server['host']}\n"
            f"  Port: {server.get('port', 22)}\n"
            f"  User: {server['user']}\n"
            f"  Key: {server.get('key_path', '~/.ssh/id_ed25519')}"
        ), True

    lines = [f"Servers ({len(active)}):"]
    for s in active:
        lines.append(f"  [{s['name']}] {s['user']}@{s['host']}:{s.get('port', 22)}")
    return '\n'.join(lines), True


def _run_command(server_name, command, timeout=30):
    blocked = _check_blacklist(command)
    if blocked:
        logger.warning(f"Command blocked by blacklist: {command!r} matched {blocked!r}")
        return f"Command blocked by safety filter (matched: {blocked}). Edit blacklist in Settings > Plugins > SSH.", False

    max_timeout = _get_max_timeout()
    timeout = min(max(5, timeout), max_timeout)

    from core.credentials_manager import credentials
    all_servers = credentials.get_ssh_servers()
    active = [s for s in all_servers if s.get('enabled', True)]
    server = next((s for s in active if s['name'].lower() == server_name.lower()), None)
    if not server:
        active_names = [s['name'] for s in active]
        if active_names:
            return f"Server '{server_name}' not found. Available: {', '.join(active_names)}", False
        return "No servers available.", False

    return _run_remote(server, command, timeout)


def _login_dir():
    """A private folder for one call's login material. Lives beside the
    credentials (the home folder is rarely mounted noexec; /tmp can be)."""
    import tempfile
    from core.setup import CONFIG_DIR
    base = Path(CONFIG_DIR) / 'run'
    base.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(base, 0o700)
    return Path(tempfile.mkdtemp(prefix='ssh-', dir=str(base)))


def _private_file(folder, name, text, mode=0o600):
    path = folder / name
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return path


def _login_args(auth, folder):
    """(ssh options, env) for a login that needs material on disk.
    auth: {'mode': 'key_paste' | 'password', 'secret': str}"""
    secret = str(auth.get('secret') or '')
    if auth.get('mode') == 'key_paste':
        key = secret.replace('\r\n', '\n').strip() + '\n'     # ssh wants the last newline
        path = _private_file(folder, 'key', key)
        return ['-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', str(path)], None
    # password: OpenSSH's own askpass hook (8.4+). ssh runs the helper and
    # reads the password from its output. No sshpass, no second SSH library.
    held = _private_file(folder, 'secret', secret)
    if os.name == 'nt':
        helper = _private_file(folder, 'askpass.cmd', '@type "%SAPPHIRE_SSH_SECRET%"\r\n', 0o700)
    else:
        helper = _private_file(folder, 'askpass.sh', '#!/bin/sh\ncat "$SAPPHIRE_SSH_SECRET"\n', 0o700)
    env = dict(os.environ, SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE='force',
               SAPPHIRE_SSH_SECRET=str(held))
    return ['-o', 'BatchMode=no', '-o', 'PubkeyAuthentication=no',
            '-o', 'PreferredAuthentications=password,keyboard-interactive',
            '-o', 'NumberOfPasswordPrompts=1'], env


def _login_opts(server, auth):
    """(options, key options, env, folder) for one ssh OR scp call. auth=None
    is the classic path: the server's key_path when it has one, else whatever
    keys ssh already knows. auth={'mode', 'secret'} is a login whose material
    lives in the secrets store (a pasted key, a password); it sits in a
    private `folder` for the length of the call — the caller removes it."""
    opts = ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=5']
    key, env, folder = [], None, None
    if auth and auth.get('mode') in ('key_paste', 'password'):
        folder = _login_dir()
        more, env = _login_args(auth, folder)
        opts.extend(more)
    else:
        opts.extend(['-o', 'BatchMode=yes'])
        key_path = server.get('key_path', '')
        if key_path:
            key = ['-i', str(Path(key_path).expanduser())]
    return opts, key, env, folder


def _exec(server, tool, rest, timeout, auth, what):
    """Run `tool` (ssh: -p, scp: -P) with the login in place and `rest`
    after it. Returns (CompletedProcess, None) or (None, error text). The
    ONE place a login touches a process: material on disk only for the
    call, errors that happened with material in play never carry their text."""
    folder = None
    try:
        opts, key, env, folder = _login_opts(server, auth)
        port = str(server.get('port', 22))
        cmd = [tool, *opts, '-P' if tool == 'scp' else '-p', port, *key, *rest]
        extra = {'env': env, 'stdin': subprocess.DEVNULL} if folder else {}
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding='utf-8', errors='replace', **extra), None
    except subprocess.TimeoutExpired:
        logger.warning(f"SSH {what} timed out after {timeout}s")
        return None, f"[{server['name']}] {what} timed out after {timeout}s."
    except FileNotFoundError:
        return None, "SSH client not found on system. Is OpenSSH installed?"
    except Exception as e:
        if folder:      # login material was in play: the error text stays out
            logger.error(f"SSH error: {type(e).__name__}")
            return None, f"SSH error: {type(e).__name__}"
        logger.error(f"SSH error: {e}", exc_info=True)
        return None, f"SSH error: {e}"
    finally:
        if folder:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)


def _run_remote(server, command, timeout, auth=None, raw=False):
    """Run command on remote server via SSH. The ONE ssh runner.
    raw=True answers (stdout, ok) for code that parses the output (the
    backup target); the usual answer is the formatted report for her."""
    host, user = server['host'], server['user']
    logger.info(f"SSH [{server['name']}] ({user}@{host}): {command[:100]}")
    result, err = _exec(server, 'ssh', [f'{user}@{host}', command], timeout, auth, 'Command')
    if err:
        return err, False
    if raw:
        ok = result.returncode == 0
        return (result.stdout if ok else (result.stderr.strip() or result.stdout)[:400]), ok
    return _format_output(server['name'], host, command, result)


def _copy_remote(server, src, dst, timeout, auth=None, get=False):
    """scp one file: local src → remote dst (get=False) or remote src →
    local dst (get=True). Paths are passed as given — the backup target
    only ever uses names it validated. Returns (text, ok)."""
    host, user = server['host'], server['user']
    a, b = (f'{user}@{host}:{src}', str(dst)) if get else (str(src), f'{user}@{host}:{dst}')
    logger.info(f"SCP [{server['name']}] {'from' if get else 'to'} {user}@{host}: {src if get else dst}")
    result, err = _exec(server, 'scp', ['-q', a, b], timeout, auth, 'Copy')
    if err:
        return err, False
    return (result.stderr.strip() or 'copied')[:400], result.returncode == 0


def _format_output(name, host, command, result):
    """Format subprocess result with truncation."""
    output = result.stdout
    stderr = result.stderr.strip()
    exit_code = result.returncode

    parts = []
    if output:
        parts.append(output)
    if stderr and exit_code != 0:
        parts.append(f"STDERR: {stderr}")
    full_output = '\n'.join(parts) if parts else '(no output)'

    limit = _get_output_limit()
    truncated = False
    if len(full_output) > limit:
        full_output = full_output[:limit]
        truncated = True

    header = f"[{name}] ({host}) $ {command}\nExit code: {exit_code}"
    if truncated:
        header += f" (output truncated to {limit} chars)"

    return f"{header}\n\n{full_output}", exit_code == 0


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        if function_name == "ssh_get_servers":
            return _get_servers(name=arguments.get('name'))
        elif function_name == "ssh_run_command":
            server = arguments.get('server')
            command = arguments.get('command')
            if not server:
                return "server name is required.", False
            if not command:
                return "command is required.", False
            timeout = arguments.get('timeout', 30)
            return _run_command(server, command, timeout)
        else:
            return f"Unknown SSH function '{function_name}'.", False
    except Exception as e:
        logger.error(f"SSH tool error in {function_name}: {e}", exc_info=True)
        return f"SSH error: {e}", False
