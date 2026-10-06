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
#
# Power (restart, shutdown) is a capability of its own, so the user can lock
# it for her on one machine and leave it open on another. Its two commands
# are the USER's words, like a premade command. It starts out locked.
import base64
import hashlib
import re
import shlex

from core.backup_targets import Target
from core.devices.storage import keep_from
from plugins.ssh.tools import ssh_tool

NAME_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,40}$')
RESERVED = ('run',)
SLOT = '{value}'
# A backup folder on the machine: absolute, or relative to the login's home.
# No spaces, no quotes, no ~ — scp hands the path to the remote as-is, and
# its sftp mode cannot expand ~ on every server (2026-10-06).
DIR_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}$|^/[A-Za-z0-9_./-]{1,200}$')
FILE_RE = re.compile(r'^[A-Za-z0-9_.-]{1,96}$')
COPY_WAIT = 20 * 60          # a 70 MB blob over a slow link


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
    folder = str(config.get('backup_dir') or '').strip().rstrip('/')
    if folder and (not DIR_RE.match(folder) or '..' in folder.split('/')):
        return config, ("The backup folder is a plain path: absolute (/srv/sapphire-backups) or "
                        "relative to the user's home (backups/sapphire). No spaces, quotes or ~.")
    config['backup_dir'] = folder
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


POWER = {'restart': ('restart_command', 'sudo -n shutdown -r +1'),
         'shutdown': ('shutdown_command', 'sudo -n shutdown -h +1')}


def _power_command(config, action):
    """The user's command for this action. A device saved before power
    existed has no such setting yet and gets the usual command. An empty
    setting means the user took it away."""
    key, usual = POWER[action]
    return str(config[key] if key in config else usual).strip()


def describe(device, config):
    actions = {}
    for c in config.get('commands', []):
        actions[c['name']] = {'help': c['command'][:60],
                              'example': '<value>' if SLOT in c['command'] else '',
                              'values': '<value>' if SLOT in c['command'] else ''}
    if config.get('allow_all'):
        actions['run'] = {'help': 'any command, safety filter applies', 'example': 'uptime',
                          'values': '<command>'}
    told = {'ssh': {'label': 'SSH', 'help': 'run commands on it', 'actions': actions}}
    power = {name: {'help': _power_command(config, name)[:60], 'example': ''}
             for name in POWER if _power_command(config, name)}
    if power:
        told['power'] = {'label': 'Power', 'help': 'restart it or shut it down', 'actions': power}
    if config.get('backup_dir'):
        told['storage'] = {'label': 'Backup', 'help': 'Sapphire keeps sealed backups in a folder there',
                           'actions': {'backup': {'help': 'make a backup and send it there now', 'example': ''},
                                       'list': {'help': 'what is there and how much room is left', 'example': ''}}}
    return told


def status(device, config, secrets):
    server, auth, error = _login(device, config, secrets)
    where = f"{server['user']}@{server['host']}:{server['port']}"
    if error:
        return {'online': False, 'detail': error}
    text, ok = ssh_tool._run_remote(server, 'echo ok', 10, auth)
    out = {'online': bool(ok), 'detail': where if ok else f"{where} - {_reason(text)}"}
    if ok and config.get('backup_dir'):
        try:
            info = SshTarget(server, auth, config, device).info()
            out['readings'] = {'backup folder': f"{config['backup_dir']} ({len(info['files'])} there, "
                                                f"{info['free_bytes'] // (1024 * 1024):,} MB free)"}
        except Problem as e:
            out['readings'] = {'backup folder': str(e)}
    return out


class Problem(Exception):
    pass


class SshTarget(Target):
    """A folder on an SSH machine as a place backups go (remote: sealed
    always). scp carries the bytes; a short ssh afterwards checks the sha256
    and moves .partial into place, the way the satellite board does."""
    kind = 'ssh'
    remote = True

    def __init__(self, server, auth, config, device):
        self.server, self.auth = server, auth
        self.dir = str(config.get('backup_dir') or '').rstrip('/')
        self.label = device.get('label') or device.get('id') or server['host']
        self.keep = keep_from(config)
        if not self.dir:
            raise Problem("No backup folder is set for this machine.")

    def _sh(self, command, timeout=60):
        text, ok = ssh_tool._run_remote(self.server, command, timeout, self.auth, raw=True)
        if not ok:
            raise Problem(f"{self.label}: {str(text).strip().splitlines()[-1][:200] if str(text).strip() else 'no answer'}")
        return text

    def _path(self, name):
        if not FILE_RE.match(name or ''):
            raise Problem(f"not a backup name: {name!r}")
        return f"{self.dir}/{name}"

    def info(self):
        d = shlex.quote(self.dir)
        out = self._sh(f"mkdir -p {d} && cd {d} && ls -ln && echo __DF__ && df -Pk . | tail -1")
        listing, _, df = out.partition('__DF__')
        files = {}
        for line in listing.splitlines():
            parts = line.split()                       # -rw-r--r-- 1 uid gid SIZE mon d time NAME
            if len(parts) >= 9 and parts[0][0] == '-' and parts[4].isdigit():
                files[parts[8]] = int(parts[4])
        dparts = df.split()                            # fs 1024-blocks used AVAIL cap mount
        free_kb = int(dparts[3]) if len(dparts) >= 4 and dparts[3].isdigit() else 0
        return {'files': files, 'free_bytes': free_kb * 1024, 'path': self.dir}

    def sizes(self):
        return self.info()['files']

    def names(self):
        return list(self.sizes())

    def put(self, path, name):
        self.check(path, name)
        dst = self._path(name)
        with open(path, 'rb') as f:
            want = hashlib.file_digest(f, 'sha256').hexdigest()
        self._sh(f"mkdir -p {shlex.quote(self.dir)}")
        text, ok = ssh_tool._copy_remote(self.server, path, dst + '.partial', COPY_WAIT, self.auth)
        if not ok:
            self._sh(f"rm -f {shlex.quote(dst + '.partial')}")
            raise Problem(f"{self.label}: copy failed: {text}")
        p = shlex.quote(dst + '.partial')
        got = self._sh(f"(sha256sum {p} || shasum -a 256 {p}) 2>/dev/null | cut -d' ' -f1").strip()
        if got != want:
            self._sh(f"rm -f {p}")
            raise Problem(f"{self.label}: {name} arrived corrupt (sha256 mismatch); removed it")
        self._sh(f"mv -f {p} {shlex.quote(dst)}")

    def delete(self, name):
        self._sh(f"rm -f {shlex.quote(self._path(name))}")

    def get(self, name, dst):
        text, ok = ssh_tool._copy_remote(self.server, self._path(name), dst, COPY_WAIT, self.auth, get=True)
        if not ok:
            raise Problem(f"{self.label}: copy back failed: {text}")


def storage_target(device, config, secrets):
    """Core asks for this when the machine has `storage` (a backup folder)."""
    if not config.get('backup_dir'):
        return None
    server, auth, error = _login(device, config, secrets)
    if error:
        raise Problem(error)
    return SshTarget(server, auth, config, device)


def run(device, capability, action, value, config, secrets, call_tool):
    if capability == 'storage':
        from core.devices import storage as st
        try:
            target = storage_target(device, config, secrets)
        except Problem as e:
            return str(e), False
        if target is None:
            return "No backup folder is set for this machine. Add one in Settings > Devices.", False
        if action == 'backup':
            return st.backup_now(target)
        if action == 'list':
            try:
                return st.listing_text(target, target.info()), True
            except Problem as e:
                return str(e), False
        return f"'{action}' is not a backup action.", False
    if capability == 'power':
        command = _power_command(config, action) if action in POWER else ''
        if not command:
            return f"No {action} command is set for this machine.", False
    elif action == 'run':
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
