# core/devices/drivers/computer.py - this computer (tmp/device-manager-plan.md)
#
# The machine Sapphire runs on: its volume, its sound outputs, its screen,
# and its power. No address and no key: there is nothing to reach.
#
# Sound is set through whatever the machine has:
#   Linux    wpctl (PipeWire), then pactl (PulseAudio), then amixer (ALSA)
#   Windows  the default output, through PowerShell
# The screen is seen through the screenshot plugin's own tool, so there is
# ONE screenshot program in Sapphire. With that plugin off, this driver has
# no screen.
# Storage (2026-10-06): a folder on this machine - a USB stick, a NAS mount,
# a second disk - as a place core/backup_targets ships backups to. The only
# target kind that may hold plain backups (its own switch, off by default):
# the stick is in your hand, it never left the house.
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from core.backup_targets import Target
from core.devices.storage import KEEP_FIELDS, keep_from

logger = logging.getLogger(__name__)

SCREEN_TOOL = 'get_screenshot'

SPEC = {
    'label': 'This computer',
    'icon': '\U0001f4bb',
    'capabilities': ['sound', 'screen', 'power', 'storage'],
    'locked_by_default': ['power'],
    'uses_tools': [SCREEN_TOOL],
    'config_schema': [
        {'key': 'step', 'type': 'number', 'label': 'Volume step', 'default': 5, 'min': 1, 'max': 25,
         'capability': 'sound', 'help': 'How far "up" and "down" move the volume, in percent.'},
        {'key': 'loudest', 'type': 'number', 'label': 'Loudest Sapphire may set', 'default': 100,
         'min': 10, 'max': 100, 'capability': 'sound',
         'help': 'In percent. She is held to this. Your own volume keys are not.'},
        {'key': 'backup_path', 'type': 'string', 'label': 'Backup folder', 'capability': 'storage', 'tab': 'Backup',
         'placeholder': '/media/usb/sapphire-backups  or  D:\\sapphire-backups',
         'help': 'A folder that already exists: on a USB stick, a NAS mount, a second disk. '
                 'Empty = no backups go here. A folder inside Sapphire\'s own user/ is refused.'},
        {'key': 'backup_encrypt', 'type': 'boolean', 'label': 'Seal them', 'capability': 'storage', 'tab': 'Backup',
         'default': False,
         'help': 'On = sealed .sapphirebak files that only the backup password opens. '
                 'Off = plain .tar.gz you can open anywhere (this folder is on your own machine). '
                 'A network share (SMB, NFS, sshfs) is another machine: it is always sealed.'},
        *KEEP_FIELDS,
    ],
}

QUICK = 6                  # seconds for one command
POWER_DELAY = 5            # the answer goes out first, then the machine goes down
_WPCTL_SINK = '@DEFAULT_AUDIO_SINK@'
_PACTL_SINK = '@DEFAULT_SINK@'
_POWER = {'restart': ('reboot', 'CanReboot'), 'shutdown': ('poweroff', 'CanPowerOff'),
          'sleep': ('suspend', 'CanSuspend')}


class Problem(Exception):
    """A reason fit to show as it is."""


def _windows():
    return platform.system() == 'Windows'


def _sh(cmd, timeout=QUICK, text_in=None):
    """(exit code, output, errors). A missing program is exit code 127."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=text_in)
        return r.returncode, r.stdout or '', r.stderr or ''
    except FileNotFoundError:
        return 127, '', f"{cmd[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, '', f"{cmd[0]} gave no answer within {timeout}s"


def _must(cmd, what, **kw):
    code, out, err = _sh(cmd, **kw)
    if code != 0:
        why = (err or out).strip().splitlines()
        raise Problem(f"{what} failed: {(why[-1] if why else f'exit code {code}')[:160]}")
    return out


# --- sound: one small class per way of doing it --------------------------------

class Wpctl:
    name = 'wpctl'

    def read(self):
        out = _must(['wpctl', 'get-volume', _WPCTL_SINK], 'Reading the volume')
        m = re.search(r'Volume:\s*([0-9.]+)', out)
        if not m:
            raise Problem("The volume could not be read.")
        return round(float(m.group(1)) * 100), 'MUTED' in out.upper()

    def set(self, percent):
        _must(['wpctl', 'set-volume', '-l', '1.0', _WPCTL_SINK, f"{percent}%"], 'Setting the volume')

    def mute(self, on):
        _must(['wpctl', 'set-mute', _WPCTL_SINK, '1' if on else '0'], 'Muting')

    def outputs(self):
        """[(id, name, is the one in use)]"""
        out = _must(['wpctl', 'status'], 'Listing the outputs')
        found, inside = [], False
        for line in out.splitlines():
            if re.search(r'Sinks:\s*$', line):
                inside = True
                continue
            if inside:
                m = re.match(r'^[\s│├└─]*(\*)?\s*(\d+)\.\s+(.+?)\s*(?:\[vol:.*\])?\s*$', line)
                if not m:
                    if found or re.search(r'\w+:\s*$', line):
                        break
                    continue
                found.append((m.group(2), m.group(3).strip(), bool(m.group(1))))
        return found

    def use(self, ident):
        _must(['wpctl', 'set-default', str(ident)], 'Switching the output')


class Pactl:
    name = 'pactl'

    def read(self):
        out = _must(['pactl', 'get-sink-volume', _PACTL_SINK], 'Reading the volume')
        m = re.search(r'(\d+)%', out)
        if not m:
            raise Problem("The volume could not be read.")
        muted = _must(['pactl', 'get-sink-mute', _PACTL_SINK], 'Reading the mute switch')
        return int(m.group(1)), 'yes' in muted.lower()

    def set(self, percent):
        _must(['pactl', 'set-sink-volume', _PACTL_SINK, f"{percent}%"], 'Setting the volume')

    def mute(self, on):
        _must(['pactl', 'set-sink-mute', _PACTL_SINK, '1' if on else '0'], 'Muting')

    def outputs(self):
        now = _must(['pactl', 'get-default-sink'], 'Listing the outputs').strip()
        out = _must(['pactl', 'list', 'sinks'], 'Listing the outputs')
        found = []
        for block in re.split(r'^Sink #', out, flags=re.M)[1:]:
            name = re.search(r'^\s*Name:\s*(.+)$', block, re.M)
            desc = re.search(r'^\s*Description:\s*(.+)$', block, re.M)
            if name:
                ident = name.group(1).strip()
                found.append((ident, (desc.group(1).strip() if desc else ident), ident == now))
        return found

    def use(self, ident):
        _must(['pactl', 'set-default-sink', str(ident)], 'Switching the output')


class Amixer:
    name = 'amixer'

    def read(self):
        out = _must(['amixer', 'sget', 'Master'], 'Reading the volume')
        m = re.search(r'\[(\d+)%\]', out)
        if not m:
            raise Problem("The volume could not be read.")
        return int(m.group(1)), '[off]' in out

    def set(self, percent):
        _must(['amixer', '-q', 'sset', 'Master', f"{percent}%"], 'Setting the volume')

    def mute(self, on):
        _must(['amixer', '-q', 'sset', 'Master', 'mute' if on else 'unmute'], 'Muting')

    def outputs(self):
        return None                      # plain ALSA has no list worth offering

    use = None


# The default output's volume on Windows, through its own sound interface.
# No extra program is needed: PowerShell compiles this once per call.
_WINDOWS_AUDIO = r'''
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h(); int i();
  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
  int j();
  int GetMasterVolumeLevelScalar(out float pfLevel);
  int k(); int l(); int m(); int n();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);
  int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int f(); int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class SapphireAudio {
  static IAudioEndpointVolume Vol() {
    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice dev = null;
    Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out dev));
    IAudioEndpointVolume epv = null;
    var epvid = typeof(IAudioEndpointVolume).GUID;
    Marshal.ThrowExceptionForHR(dev.Activate(ref epvid, 23, 0, out epv));
    return epv;
  }
  public static float Volume {
    get { float v = -1; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevelScalar(out v)); return v; }
    set { Marshal.ThrowExceptionForHR(Vol().SetMasterVolumeLevelScalar(value, System.Guid.Empty)); }
  }
  public static bool Mute {
    get { bool mute; Marshal.ThrowExceptionForHR(Vol().GetMute(out mute)); return mute; }
    set { Marshal.ThrowExceptionForHR(Vol().SetMute(value, System.Guid.Empty)); }
  }
}
'@
'''


class Windows:
    name = 'windows'

    def _ps(self, line, what):
        return _must(['powershell', '-NoProfile', '-NonInteractive', '-Command', '-'], what,
                     timeout=20, text_in=_WINDOWS_AUDIO + line + '\n')

    def read(self):
        out = self._ps('"{0} {1}" -f [math]::Round([SapphireAudio]::Volume * 100), '
                       '[int][SapphireAudio]::Mute', 'Reading the volume')
        m = re.search(r'(\d+)\s+([01])\s*$', out.strip())
        if not m:
            raise Problem("The volume could not be read.")
        return int(m.group(1)), m.group(2) == '1'

    def set(self, percent):
        self._ps(f"[SapphireAudio]::Volume = {int(percent) / 100:.2f}", 'Setting the volume')

    def mute(self, on):
        self._ps(f"[SapphireAudio]::Mute = ${'true' if on else 'false'}", 'Muting')

    def outputs(self):
        return None

    use = None


def _sound():
    """The way this machine sets its volume, or Problem."""
    if _windows():
        return Windows()
    for cls in (Wpctl, Pactl, Amixer):
        if shutil.which(cls.name):
            way = cls()
            try:
                way.read()
                return way
            except Problem:
                continue                 # installed, and its sound server is not running
    raise Problem("This computer has no sound control I can use. On Linux it needs one of "
                  "wpctl, pactl or amixer.")


def _percent(value, what='The volume'):
    text = str(value or '').strip().rstrip('%').strip()
    try:
        n = float(text)
    except ValueError:
        raise Problem(f"{what} is a number from 0 to 100, not '{value}'.")
    if not 0 <= n <= 100:
        raise Problem(f"{what} is a number from 0 to 100, not {text}.")
    return int(round(n))


def _limit(config):
    try:
        return max(10, min(100, int(float(config.get('loudest') or 100))))
    except (TypeError, ValueError):
        return 100


def _step(config):
    try:
        return max(1, min(25, int(float(config.get('step') or 5))))
    except (TypeError, ValueError):
        return 5


def _said(way):
    level, muted = way.read()
    return f"Volume {level}%" + (', muted' if muted else '')


def _set_volume(way, want, config):
    top = _limit(config)
    held = want > top
    way.set(min(want, top))
    return _said(way) + (f". {top}% is the loudest I am allowed here" if held else '') + '.'


def _pick_output(way, want):
    """The output she means: its number in the list, its id, or part of its name."""
    found = way.outputs() or []
    text = str(want or '').strip().lower()
    if not text:
        raise Problem("use: the value is an output's number or part of its name. "
                      "'outputs' lists them.")
    if text.isdigit() and 1 <= int(text) <= len(found):
        return found[int(text) - 1]
    hits = [o for o in found if text == o[0].lower() or text in o[1].lower()]
    if len(hits) == 1:
        return hits[0]
    names = ', '.join(f"{i}. {o[1]}" for i, o in enumerate(found, 1))
    if not hits:
        raise Problem(f"No output matches '{want}'. Outputs: {names}")
    raise Problem(f"'{want}' matches {len(hits)} outputs. Use its number. Outputs: {names}")


# --- the screen ----------------------------------------------------------------

def _can_see():
    """True when the screenshot plugin's tool is there to be used."""
    try:
        from core.api_fastapi import get_system
        return bool(get_system().llm_chat.function_manager.tool_plugin(SCREEN_TOOL))
    except Exception:
        return False


# --- power ---------------------------------------------------------------------

def _allowed(check):
    """Does this machine let Sapphire do it without a password?"""
    code, out, _ = _sh(['busctl', 'call', 'org.freedesktop.login1', '/org/freedesktop/login1',
                        'org.freedesktop.login1.Manager', check])
    return code == 0 and '"yes"' in out


def _go_down(cmd):
    time.sleep(POWER_DELAY)
    logger.warning(f"[DEVICES] this computer: {' '.join(cmd)}")
    code, out, err = _sh(cmd, timeout=30)
    if code != 0:
        logger.error(f"[DEVICES] this computer: {' '.join(cmd)} failed: {(err or out).strip()[:200]}")


def _power(action):
    if _windows():
        flags = {'restart': '/r', 'shutdown': '/s'}.get(action)
        if not flags:
            raise Problem(f"This computer has no power / {action}.")
        _must(['shutdown', flags, '/t', '30'], f"The {action}")
        return (f"This computer will {action} in 30 seconds. I run on it, so I go down with it. "
                "To cancel: shutdown /a")
    unit, check = _POWER[action]
    if not shutil.which('systemctl'):
        raise Problem("This computer has no systemctl, so I cannot do that here.")
    if shutil.which('busctl') and not _allowed(check):
        raise Problem(f"This computer will not let me {action} it without a password.")
    threading.Thread(target=_go_down, args=(['systemctl', unit],), daemon=True,
                     name='computer-power').start()
    if action == 'sleep':
        return f"This computer goes to sleep in {POWER_DELAY} seconds. I sleep with it, and wake when it wakes."
    back = 'I come back when it has started again' if action == 'restart' \
        else 'Someone has to switch it on again'
    return (f"This computer will {action} in {POWER_DELAY} seconds. I run on it, so I go down "
            f"with it. {back}.")


# --- the driver ----------------------------------------------------------------

def describe(device, config):
    told = {'sound': {'label': 'Sound', 'help': 'the volume of everything it plays, and its outputs',
                      'actions': {
        'set': {'help': 'the volume', 'example': '40', 'values': '<0-100>'},
        'read': {'help': 'the volume now', 'example': ''},
        'up': {'help': 'a step louder', 'example': ''},
        'down': {'help': 'a step quieter', 'example': ''},
        'mute': {'help': 'silence it', 'example': ''},
        'unmute': {'help': 'sound back on', 'example': ''},
    }}}
    if not _windows():
        told['sound']['actions']['outputs'] = {'help': 'where sound can come out', 'example': ''}
        told['sound']['actions']['use'] = {'help': "switch to an output. 'outputs' numbers them",
                                           'example': '1', 'values': '<number | name>'}
    if _can_see():
        told['screen'] = {'label': 'Screen', 'help': 'see what is on its screen', 'actions': {
            'look': {'help': 'take one picture of the whole screen and see it', 'example': ''},
        }}
    power = {'restart': {'help': 'restart this computer. You go down with it', 'example': ''},
             'shutdown': {'help': 'switch it off. Someone has to switch it on again', 'example': ''}}
    if not _windows():
        power['sleep'] = {'help': 'put it to sleep', 'example': ''}
    told['power'] = {'label': 'Power', 'help': 'restart it, shut it down, or sleep', 'actions': power}
    if str(config.get('backup_path') or '').strip():     # no folder set = no Backup actions (the fields still show)
        told['storage'] = {'label': 'Backup', 'help': 'copies of her memory in a folder here: a stick, a NAS',
                           'actions': {
            'backup': {'help': 'back everything up into that folder now', 'example': ''},
            'list': {'help': 'what is in the folder and how much room is left', 'example': ''},
        }}
    return told


# --- backups into a folder here ----------------------------------------------

NETWORK_FS = {'cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', 'fuse.sshfs', 'sshfs', 'afpfs', 'davfs',
              'fuse.davfs', 'fuse.rclone', '9p', 'ceph', 'glusterfs', 'fuse.gvfsd-fuse'}


def is_network_mount(path):
    """True when `path` sits on a network share (SMB/NFS/sshfs mount, a UNC
    path or a mapped drive on Windows): bytes written there leave this
    machine, so the never-plaintext-off-box rule applies (2026-10-06)."""
    raw = str(path)
    if os.name == 'nt':
        if raw.startswith('\\\\') or raw.startswith('//'):
            return True
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(raw))[0] + '\\'
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4          # DRIVE_REMOTE
        except Exception:
            return False
    try:
        target = os.path.realpath(raw)
        best, fstype = '', ''
        with open('/proc/mounts', encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mnt = parts[1].replace('\\040', ' ')
                if (target == mnt or target.startswith(mnt.rstrip('/') + '/')) and len(mnt) > len(best):
                    best, fstype = mnt, parts[2]
        return fstype in NETWORK_FS or fstype.startswith('fuse.') and 'ssh' in fstype
    except OSError:
        return False


class FolderTarget(Target):
    """A folder on this machine as a place backups go. remote=False: the one
    kind that may hold plain backups, by its own `backup_encrypt` switch —
    unless the folder is a network mount, which is another machine with a
    local name: then the target is remote and seals whatever the switch says."""
    kind = 'folder'
    remote = False

    def __init__(self, device, config):
        self.label = f"{device['id']}:{config.get('backup_path')}"
        self.keep = keep_from(config)
        self.encrypt = bool(config.get('backup_encrypt', False))
        self.path = _folder(config)
        if is_network_mount(self.path):
            self.remote = True

    def info(self):
        try:
            u = shutil.disk_usage(self.path)
            return {'free_bytes': u.free, 'total_bytes': u.total, 'path': str(self.path)}
        except OSError:
            return {'path': str(self.path)}

    def names(self):
        return list(self.sizes())

    def sizes(self):
        return {p.name: p.stat().st_size for p in self.path.iterdir() if p.is_file()}

    def put(self, path, name):
        self.check(path, name)
        part = self.path / (name + '.partial')
        shutil.copyfile(path, part)
        os.replace(part, self.path / name)

    def delete(self, name):
        if '/' not in name and '\\' not in name:
            (self.path / name).unlink(missing_ok=True)

    def get(self, name, dst):
        shutil.copyfile(self.path / name, dst)


def _folder(config):
    """The backup folder, which must already exist (a stick that is not
    plugged in must not become a folder on the main disk) and must not be
    inside Sapphire's own user/. Raises Problem."""
    raw = str(config.get('backup_path') or '').strip()
    if not raw:
        raise Problem("No backup folder is set. Enter one in Settings > Devices.")
    p = Path(os.path.expanduser(raw))
    if not p.is_dir():
        raise Problem(f"The backup folder {raw} is not there. Is the drive plugged in / mounted?")
    try:
        from core.backup import backup_manager
        if p.resolve().is_relative_to(backup_manager.user_dir.resolve()):
            raise Problem("The backup folder cannot be inside Sapphire's own user/ folder.")
    except OSError:
        pass
    if not os.access(p, os.W_OK):
        raise Problem(f"The backup folder {raw} cannot be written.")
    return p


def storage_target(device, config, secrets):
    """Core's door (core/devices/storage.py): this folder as a backup target,
    or None when no folder is set. A folder that is set but missing raises,
    so the nightly log says why nothing landed."""
    if not str(config.get('backup_path') or '').strip():
        return None
    return FolderTarget(device, config)


def status(device, config, secrets):
    try:
        way = _sound()
        level, muted = way.read()
    except Problem as e:
        return {'online': True, 'detail': platform.node() or 'this computer',
                'readings': {'sound': str(e)}}
    readings = {'volume': f"{level}%" + (', muted' if muted else '')}
    try:
        now = [o for o in (way.outputs() or []) if o[2]]
        if now:
            readings['sound comes out of'] = now[0][1][:60]
    except Problem:
        pass
    readings['screen'] = 'can be seen' if _can_see() else 'the screenshot plugin is off'
    if str(config.get('backup_path') or '').strip():
        try:
            from core.devices.storage import _gb
            info = FolderTarget(device, config).info()
            readings['backup folder'] = f"{_gb(info['free_bytes'])} free" if 'free_bytes' in info else 'there'
        except Problem as e:
            readings['backup folder'] = str(e)
    return {'online': True, 'readings': readings,
            'detail': f"{platform.node() or 'this computer'}, {platform.system()}"}


def run(device, capability, action, value, config, secrets, call_tool):
    try:
        if capability == 'sound':
            way = _sound()
            if action == 'read':
                return _said(way) + '.', True
            if action == 'set':
                if not str(value or '').strip():
                    return "set: the value is the volume, 0 to 100. Example: 40", True
                return _set_volume(way, _percent(value), config), True
            if action in ('up', 'down'):
                level, _ = way.read()
                move = _step(config) if action == 'up' else -_step(config)
                return _set_volume(way, max(0, min(100, level + move)), config), True
            if action in ('mute', 'unmute'):
                way.mute(action == 'mute')
                return _said(way) + '.', True
            if action in ('outputs', 'use'):
                found = way.outputs()
                if found is None or way.use is None:
                    return "This computer's outputs cannot be listed from here.", False
                if action == 'outputs':
                    if not found:
                        return "No outputs were found.", True
                    return 'Outputs:\n' + '\n'.join(
                        f"  {i}. {name}" + ('  (in use)' if used else '')
                        for i, (_, name, used) in enumerate(found, 1)), True
                ident, name, used = _pick_output(way, value)
                if not used:
                    way.use(ident)
                return f"Sound now comes out of {name}. {_said(way)}.", True
        if capability == 'screen' and action == 'look':
            if not _can_see():
                return "The screenshot plugin is off, so I cannot see this screen.", False
            return call_tool(SCREEN_TOOL, {'source': 'local'})
        if capability == 'power' and action in _POWER:
            return _power(action), True
        if capability == 'storage':
            from core.devices import storage as st
            target = FolderTarget(device, config)      # Problem when unset or missing
            if action == 'backup':
                return st.backup_now(target)
            if action == 'list':
                return st.listing_text(target, target.info()), True
    except Problem as e:
        return str(e), False
    return f"This computer has no {capability} / {action}.", False
