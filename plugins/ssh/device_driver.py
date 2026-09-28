# plugins/ssh/device_driver.py - the SSH device driver (tmp/device-manager-plan.md)
#
# Declared in this plugin's manifest (capabilities.devices) and run by the
# device engine in core. It builds no ssh command of its own: every call goes through
# ssh_tool._run_remote, the ONE runner, with the same timeout cap and output
# limit as the ssh tools.
#
# Two kinds of action:
#   premade   a name the USER wrote in Settings > Devices. Her value, if the
#             command has a {value} slot, is always shell-quoted. The user's
#             own words skip the blacklist.
#   run       any command. Exists only while "Allow any command" is checked,
#             and always passes the blacklist.
import base64
import re
import shlex

from plugins.ssh.tools import ssh_tool

NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,40}$')
RESERVED = ('run',)
SLOT = '{value}'


def _slot_is_bare(command):
    """True when every {value} slot sits OUTSIDE quotes. The value is
    shell-quoted for the user; inside the template's own quotes that quoting
    can be broken out of, so such a template is refused."""
    single = double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == '\\' and not single:
            i += 2
            continue
        if ch == "'" and not double:
            single = not single
        elif ch == '"' and not single:
            double = not double
        elif command.startswith(SLOT, i):
            if single or double:
                return False
            i += len(SLOT)
            continue
        i += 1
    return True


def validate(config):
    if not config.get('host') or not config.get('user'):
        return config, "Host and user are both needed."
    if config.get('auth') == 'key_file' and not config.get('key_path'):
        return config, "Give the path of the key file, or pick another login."
    seen = set()
    for c in config.get('commands', []):
        c['name'] = name = c.get('name', '').strip().lower()
        if not NAME_RE.fullmatch(name):
            return config, (f"Command name '{name}' is not usable. Use lowercase letters, "
                            f"digits, dashes and underscores, like close_firefox.")
        if name in RESERVED:
            return config, f"'{name}' is reserved. Pick another command name."
        if name in seen:
            return config, f"Two commands are named '{name}'."
        if not c.get('command', '').strip():
            return config, f"Command '{name}' has no command line."
        if not _slot_is_bare(c['command']):
            return config, (f"In '{name}', put {SLOT} outside of quotes. "
                            f"It is quoted for you.")
        seen.add(name)
    return config, ''


def _needs_passphrase(key):
    """True when a private key is locked with a passphrase."""
    if 'ENCRYPTED' in key:
        return True
    if 'BEGIN OPENSSH PRIVATE KEY' in key:
        try:
            body = ''.join(ln for ln in key.splitlines() if ln and not ln.startswith('-----'))
            return b'bcrypt' in base64.b64decode(body + '=' * (-len(body) % 4))[:80]
        except Exception:
            return False
    return False


def _login(device, config, secrets):
    """(server, auth, error) for the one runner."""
    server = {'name': device['id'], 'host': config.get('host', ''), 'user': config.get('user', ''),
              'port': config.get('port') or 22, 'key_path': ''}
    mode = config.get('auth') or 'auto'
    if mode == 'key_file':
        server['key_path'] = config.get('key_path', '')
        return server, None, ''
    if mode == 'key_paste':
        key = secrets.get('private_key')
        if not key:
            return server, None, "No private key is stored. Paste one in Settings > Devices."
        if _needs_passphrase(key):
            return server, None, ("The stored key is locked with a passphrase, which is not "
                                  "supported yet. Use a key without one.")
        return server, {'mode': 'key_paste', 'secret': key}, ''
    if mode == 'password':
        password = secrets.get('password')
        if not password:
            return server, None, "No password is stored. Enter one in Settings > Devices."
        return server, {'mode': 'password', 'secret': password}, ''
    return server, None, ''


def _reason(text):
    """The one useful line of a failed ssh run."""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith('STDERR:'):
            return ln[len('STDERR:'):].strip()[:200]
    return (lines[-1] if lines else 'no answer')[:200]


def describe(device, config):
    actions = {}
    for c in config.get('commands', []):
        actions[c['name']] = {'help': c['command'][:60],
                              'example': '<value>' if SLOT in c['command'] else ''}
    if config.get('allow_all'):
        actions['run'] = {'help': 'any command, safety filter applies', 'example': 'uptime'}
    return {'ssh': {'label': 'SSH', 'help': 'run commands on it', 'actions': actions}}


def status(device, config, secrets):
    server, auth, error = _login(device, config, secrets)
    where = f"{server['user']}@{server['host']}:{server['port']}"
    if error:
        return {'online': False, 'detail': error}
    text, ok = ssh_tool._run_remote(server, 'echo ok', 10, auth)
    return {'online': bool(ok), 'detail': where if ok else f"{where} - {_reason(text)}"}


def run(device, capability, action, value, config, secrets, call_tool):
    if action == 'run':
        if not config.get('allow_all'):
            return "Free-form commands are off for this device. Use a premade command.", False
        command = value.strip()
        if not command:
            return "Give the command as the value.", False
        blocked = ssh_tool._check_blacklist(command)
        if blocked:
            return (f"Command blocked by safety filter (matched: {blocked}). "
                    f"The user edits the blacklist in Settings > Plugins > SSH."), False
    else:
        made = next((c for c in config.get('commands', []) if c['name'] == action), None)
        if not made:
            return f"There is no premade command '{action}'.", False
        command = made['command']
        if SLOT in command:
            if not value.strip():
                return f"'{action}' needs a value.", False
            command = command.replace(SLOT, shlex.quote(value.strip()))

    server, auth, error = _login(device, config, secrets)
    if error:
        return error, False
    timeout = min(30, ssh_tool._get_max_timeout())
    return ssh_tool._run_remote(server, command, timeout, auth)
