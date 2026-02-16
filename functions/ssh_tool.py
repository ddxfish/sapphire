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
            "description": "Run a command on a remote server via SSH. Use the server's friendly name from ssh_get_servers(). Output is truncated if too long.",
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
    settings_file = Path(__file__).parent.parent / "user" / "settings" / "plugins" / "ssh.json"
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
    servers = credentials.get_ssh_servers()

    if not servers:
        return "No SSH servers configured. Add servers in Settings → Plugins → SSH.", True

    if name:
        server = credentials.get_ssh_server(name)
        if not server:
            names = ', '.join(s['name'] for s in servers)
            return f"Server '{name}' not found. Available: {names}", False
        return (
            f"Server: {server['name']}\n"
            f"  Host: {server['host']}\n"
            f"  Port: {server.get('port', 22)}\n"
            f"  User: {server['user']}\n"
            f"  Key: {server.get('key_path', '~/.ssh/id_ed25519')}"
        ), True

    lines = [f"SSH Servers ({len(servers)}):"]
    for s in servers:
        lines.append(f"  [{s['name']}] {s['user']}@{s['host']}:{s.get('port', 22)}")
    return '\n'.join(lines), True


def _run_command(server_name, command, timeout=30):
    from core.credentials_manager import credentials

    # Resolve server
    server = credentials.get_ssh_server(server_name)
    if not server:
        servers = credentials.get_ssh_servers()
        if servers:
            names = ', '.join(s['name'] for s in servers)
            return f"Server '{server_name}' not found. Available: {names}", False
        return "No SSH servers configured.", False

    # Blacklist check
    blocked = _check_blacklist(command)
    if blocked:
        logger.warning(f"SSH command blocked by blacklist: {command!r} matched {blocked!r}")
        return f"Command blocked by safety filter (matched: {blocked}). Edit blacklist in Settings → Plugins → SSH.", False

    # Clamp timeout
    max_timeout = _get_max_timeout()
    timeout = min(max(5, timeout), max_timeout)

    host = server['host']
    user = server['user']
    port = str(server.get('port', 22))
    key_path = server.get('key_path', '')

    # Build ssh command
    ssh_cmd = [
        'ssh',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', f'ConnectTimeout=5',
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

        output = result.stdout
        stderr = result.stderr.strip()
        exit_code = result.returncode

        # Combine stdout + stderr
        parts = []
        if output:
            parts.append(output)
        if stderr and exit_code != 0:
            parts.append(f"STDERR: {stderr}")
        full_output = '\n'.join(parts) if parts else '(no output)'

        # Truncate
        limit = _get_output_limit()
        truncated = False
        if len(full_output) > limit:
            full_output = full_output[:limit]
            truncated = True

        header = f"[{server['name']}] ({host}) $ {command}\nExit code: {exit_code}"
        if truncated:
            header += f" (output truncated to {limit} chars)"

        return f"{header}\n\n{full_output}", exit_code == 0

    except subprocess.TimeoutExpired:
        logger.warning(f"SSH command timed out after {timeout}s: {command[:100]}")
        return f"[{server['name']}] Command timed out after {timeout}s.", False
    except FileNotFoundError:
        return "SSH client not found on system. Is OpenSSH installed?", False
    except Exception as e:
        logger.error(f"SSH error: {e}", exc_info=True)
        return f"SSH error: {e}", False


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
