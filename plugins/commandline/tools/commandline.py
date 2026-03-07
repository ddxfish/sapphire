# Command Line tool — plugin tool
"""
Command Line — AI can run shell commands on the local machine.
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
EMOJI = '\U0001f4bb'
AVAILABLE_FUNCTIONS = ['run_command']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "run_command",
            "description": "Run a shell command on the local machine. Output is truncated if too long.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default 30)"
                    }
                },
                "required": ["command"]
            }
        }
    }
]

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


def _get_settings():
    """Load plugin settings."""
    settings_file = Path(__file__).parent.parent.parent.parent / "user" / "webui" / "plugins" / "commandline.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _get_blacklist():
    settings = _get_settings()
    bl = settings.get('blacklist')
    if bl is not None:
        if isinstance(bl, str):
            return [line.strip() for line in bl.split('\n') if line.strip()]
        return bl
    return DEFAULT_BLACKLIST


def _check_blacklist(command):
    """Check command against blacklist. Returns matching pattern or None."""
    for pattern in _get_blacklist():
        if not pattern:
            continue
        try:
            if re.search(pattern, command):
                return pattern
        except re.error:
            if pattern in command:
                return pattern
    return None


def _run_local(command, timeout):
    """Run command locally via subprocess."""
    logger.info(f"LOCAL $ {command[:100]}")
    try:
        import sys
        if sys.platform == 'win32':
            result = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout, shell=True,
            )
        else:
            argv = shlex.split(command)
            result = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=timeout,
            )

        output = result.stdout
        stderr = result.stderr.strip()
        exit_code = result.returncode

        parts = []
        if output:
            parts.append(output)
        if stderr and exit_code != 0:
            parts.append(f"STDERR: {stderr}")
        full_output = '\n'.join(parts) if parts else '(no output)'

        limit = _get_settings().get('output_limit', DEFAULT_OUTPUT_LIMIT)
        truncated = len(full_output) > limit
        if truncated:
            full_output = full_output[:limit]

        header = f"[localhost] $ {command}\nExit code: {exit_code}"
        if truncated:
            header += f" (output truncated to {limit} chars)"

        return f"{header}\n\n{full_output}", exit_code == 0

    except subprocess.TimeoutExpired:
        logger.warning(f"Command timed out after {timeout}s: {command[:100]}")
        return f"Command timed out after {timeout}s.", False
    except Exception as e:
        logger.error(f"Command error: {e}", exc_info=True)
        return f"Command error: {e}", False


def execute(function_name, arguments, config):
    try:
        if function_name == "run_command":
            command = arguments.get('command')
            if not command:
                return "command is required.", False
            timeout = arguments.get('timeout', 30)

            blocked = _check_blacklist(command)
            if blocked:
                logger.warning(f"Command blocked by blacklist: {command!r} matched {blocked!r}")
                return f"Command blocked by safety filter (matched: {blocked}). Edit blacklist in Settings > Plugins > Command Line.", False

            max_timeout = _get_settings().get('max_timeout', DEFAULT_MAX_TIMEOUT)
            timeout = min(max(5, timeout), max_timeout)
            return _run_local(command, timeout)
        else:
            return f"Unknown function '{function_name}'.", False
    except Exception as e:
        logger.error(f"Command line tool error: {e}", exc_info=True)
        return f"Error: {e}", False
