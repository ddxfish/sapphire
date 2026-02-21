# functions/ssh_tool.py
"""
SSH tool — AI can list servers and run commands on remote machines.
Uses system `ssh` via subprocess. Servers configured in Settings → Plugins → SSH.
Commands checked against a configurable blacklist before execution.
"""

import subprocess
import re
import shlex
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🖥️'

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
            "description": "List your configured SSH servers, or get details for a specific one by name. Call with no arguments to see all available servers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Server friendly name to get details for (optional — omit to list all)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "ssh_run_command",
            "description": "Run a command on a server by friendly name (from ssh_get_servers). Use 'localhost' for the local machine. Output is truncated if too long.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Server friendly name (from ssh_get_servers)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute on the remote server"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default 30)"
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
    settings_file = Path(__file__).parent.parent / "user" / "webui" / "plugins" / "ssh.json"
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


def _localhost_enabled():
    settings = _get_ssh_settings()
    return bool(settings.get('localhost_enabled', False))


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
    localhost = _localhost_enabled()

    active_names = [s['name'] for s in active]
    if localhost:
        active_names.insert(0, 'localhost')

    if not active and not localhost:
        return "No SSH servers available. Add or enable servers in Settings → Plugins → SSH.", True

    if name:
        if name.lower() == 'localhost':
            if not localhost:
                return "Localhost is not enabled. Enable it in Settings → Plugins → SSH.", False
            return "Server: localhost\n  Runs commands directly on this machine (no SSH).", True
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

    total = len(active) + (1 if localhost else 0)
    lines = [f"Servers ({total}):"]
    if localhost:
        lines.append("  [localhost] (local machine)")
    for s in active:
        lines.append(f"  [{s['name']}] {s['user']}@{s['host']}:{s.get('port', 22)}")
    return '\n'.join(lines), True


def _run_command(server_name, command, timeout=30):
    # Blacklist check (applies to all targets including localhost)
    blocked = _check_blacklist(command)
    if blocked:
        logger.warning(f"Command blocked by blacklist: {command!r} matched {blocked!r}")
        return f"Command blocked by safety filter (matched: {blocked}). Edit blacklist in Settings → Plugins → SSH.", False

    # Clamp timeout
    max_timeout = _get_max_timeout()
    timeout = min(max(5, timeout), max_timeout)

    # Localhost — direct subprocess, no SSH
    if server_name.lower() == 'localhost':
        if not _localhost_enabled():
            return "Localhost is not enabled. Enable it in Settings → Plugins → SSH.", False
        return _run_local(command, timeout)

    # Remote server — resolve from enabled servers only
    from core.credentials_manager import credentials
    all_servers = credentials.get_ssh_servers()
    active = [s for s in all_servers if s.get('enabled', True)]
    server = next((s for s in active if s['name'].lower() == server_name.lower()), None)
    if not server:
        active_names = [s['name'] for s in active]
        if _localhost_enabled():
            active_names.insert(0, 'localhost')
        if active_names:
            return f"Server '{server_name}' not found. Available: {', '.join(active_names)}", False
        return "No servers available.", False

    return _run_remote(server, command, timeout)


def _run_local(command, timeout):
    """Run command locally via subprocess."""
    logger.info(f"LOCAL $ {command[:100]}")

    try:
        argv = shlex.split(command)
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _format_output('localhost', 'local', command, result)

    except subprocess.TimeoutExpired:
        logger.warning(f"Local command timed out after {timeout}s: {command[:100]}")
        return f"[localhost] Command timed out after {timeout}s.", False
    except Exception as e:
        logger.error(f"Local command error: {e}", exc_info=True)
        return f"Local command error: {e}", False


def _run_remote(server, command, timeout):
    """Run command on remote server via SSH."""
    host = server['host']
    user = server['user']
    port = str(server.get('port', 22))
    key_path = server.get('key_path', '')

    ssh_cmd = [
        'ssh',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'ConnectTimeout=5',
        '-o', 'BatchMode=yes',
        '-p', port,
    ]
    if key_path:
        expanded_key = str(Path(key_path).expanduser())
        ssh_cmd.extend(['-i', expanded_key])
    ssh_cmd.append(f'{user}@{host}')
    ssh_cmd.append(command)

    logger.info(f"SSH [{server['name']}] ({user}@{host}): {command[:100]}")

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _format_output(server['name'], host, command, result)

    except subprocess.TimeoutExpired:
        logger.warning(f"SSH command timed out after {timeout}s: {command[:100]}")
        return f"[{server['name']}] Command timed out after {timeout}s.", False
    except FileNotFoundError:
        return "SSH client not found on system. Is OpenSSH installed?", False
    except Exception as e:
        logger.error(f"SSH error: {e}", exc_info=True)
        return f"SSH error: {e}", False


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
