# plugins/ssh/tests/test_device_driver.py - the SSH device driver and the one
# ssh runner's stored-login paths. subprocess is faked: no network, no real
# ssh, and login material is written under a temp folder, never ~/.config.

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plugins.ssh import device_driver as drv
from plugins.ssh.tools import ssh_tool

DEVICE = {'id': 'desktop', 'label': 'Desktop'}


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)


def cfg(**over):
    base = {'host': 'tower', 'user': 'krem', 'port': 22, 'auth': 'auto', 'key_path': '',
            'allow_all': False,
            'commands': [{'name': 'close_firefox', 'command': 'pkill firefox'},
                         {'name': 'volume', 'command': 'pactl set-sink-volume @DEFAULT_SINK@ {value}%'}]}
    base.update(over)
    return base


@pytest.fixture
def ssh(tmp_path):
    """Fake subprocess.run. Records the command, the env, and what the login
    folder held WHILE ssh was running."""
    calls = []

    def fake_run(cmd, **kw):
        held = {}
        folder = tmp_path / 'run'
        if folder.exists():
            for f in folder.rglob('*'):
                if f.is_file():
                    held[f.name] = (f.read_text(encoding='utf-8'), oct(f.stat().st_mode & 0o777))
        calls.append(SimpleNamespace(cmd=cmd, env=kw.get('env'), kw=kw, held=held))
        return SimpleNamespace(stdout='ok\n', stderr='', returncode=0)

    with patch('core.setup.CONFIG_DIR', tmp_path), \
         patch.object(ssh_tool.subprocess, 'run', fake_run), \
         patch.object(ssh_tool, '_get_ssh_settings', lambda: {}):
        yield SimpleNamespace(calls=calls, tmp=tmp_path)


def leftovers(tmp):
    folder = tmp / 'run'
    return [p for p in folder.rglob('*')] if folder.exists() else []


# --- the one runner ----------------------------------------------------------

def test_classic_path_is_unchanged(ssh):
    """auth=None must build the exact command the ssh tools always built."""
    server = {'name': 's', 'host': 'h', 'user': 'u', 'port': 2200, 'key_path': '/keys/id'}
    text, ok = ssh_tool._run_remote(server, 'uptime', 30)
    assert ok and ssh.calls[0].cmd == [
        'ssh', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=5',
        '-o', 'BatchMode=yes', '-p', '2200', '-i', '/keys/id', 'u@h', 'uptime']
    assert ssh.calls[0].env is None and 'stdin' not in ssh.calls[0].kw
    assert not (ssh.tmp / 'run').exists()
    ssh_tool._run_remote({**server, 'key_path': ''}, 'uptime', 30)
    assert '-i' not in ssh.calls[1].cmd


@pytest.mark.skipif(sys.platform == 'win32', reason='posix file modes')
def test_pasted_key_lives_for_one_call(ssh):
    server = {'name': 's', 'host': 'h', 'user': 'u', 'port': 22}
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\r\nAAAA\r\n-----END OPENSSH PRIVATE KEY-----"
    text, ok = ssh_tool._run_remote(server, 'uptime', 30, {'mode': 'key_paste', 'secret': key})
    call = ssh.calls[0]
    assert ok and 'BatchMode=yes' in call.cmd and 'IdentitiesOnly=yes' in call.cmd
    path = call.cmd[call.cmd.index('-i') + 1]
    assert path.startswith(str(ssh.tmp / 'run')) and call.cmd[-2:] == ['u@h', 'uptime']
    body, mode = call.held['key']
    assert mode == '0o600' and body.endswith('KEY-----\n') and '\r' not in body
    assert leftovers(ssh.tmp) == []                     # gone when the call ends
    assert 'AAAA' not in ' '.join(call.cmd)             # never on the command line


@pytest.mark.skipif(sys.platform == 'win32', reason='posix file modes')
def test_password_rides_the_askpass_hook(ssh):
    server = {'name': 's', 'host': 'h', 'user': 'u', 'port': 22, 'key_path': '/ignored'}
    ssh_tool._run_remote(server, 'uptime', 30, {'mode': 'password', 'secret': 'hunter2-long'})
    call = ssh.calls[0]
    assert 'BatchMode=no' in call.cmd and 'PubkeyAuthentication=no' in call.cmd
    assert 'NumberOfPasswordPrompts=1' in call.cmd and '-i' not in call.cmd
    assert call.env['SSH_ASKPASS_REQUIRE'] == 'force'
    assert call.env['SSH_ASKPASS'].endswith('askpass.sh')
    assert call.held['secret'] == ('hunter2-long', '0o600')
    assert call.held['askpass.sh'][1] == '0o700'
    assert 'hunter2-long' not in call.held['askpass.sh'][0]
    assert 'hunter2-long' not in ' '.join(call.cmd)
    assert 'hunter2-long' not in ' '.join(f"{k}={v}" for k, v in call.env.items())
    assert call.kw['stdin'] is ssh_tool.subprocess.DEVNULL
    assert leftovers(ssh.tmp) == []


def test_login_material_is_removed_even_when_ssh_blows_up(ssh):
    def boom(cmd, **kw):
        raise OSError('exploded near hunter2-long')
    with patch.object(ssh_tool.subprocess, 'run', boom):
        text, ok = ssh_tool._run_remote({'name': 's', 'host': 'h', 'user': 'u'}, 'x', 30,
                                        {'mode': 'password', 'secret': 'hunter2-long'})
    assert not ok and text == 'SSH error: OSError' and leftovers(ssh.tmp) == []


# --- validate ----------------------------------------------------------------

def test_validate_rules():
    assert drv.validate(cfg())[1] == ''
    assert 'Host and user' in drv.validate(cfg(host=''))[1]
    assert 'key file' in drv.validate(cfg(auth='key_file'))[1]
    assert drv.validate(cfg(auth='key_file', key_path='~/.ssh/id'))[1] == ''
    bad = lambda **c: drv.validate(cfg(commands=[c]))[1]
    assert 'reserved' in bad(name='run', command='x')
    assert 'not usable' in bad(name='has space', command='x')
    assert 'no command line' in bad(name='ok', command='  ')
    assert 'Two commands' in drv.validate(cfg(commands=[{'name': 'a', 'command': 'x'},
                                                        {'name': 'A', 'command': 'y'}]))[1]
    out, err = drv.validate(cfg(commands=[{'name': ' Beep ', 'command': 'beep'}]))
    assert err == '' and out['commands'][0]['name'] == 'beep'


def test_a_slot_inside_quotes_is_refused():
    ok = lambda c: drv.validate(cfg(commands=[{'name': 'a', 'command': c}]))[1] == ''
    assert ok('pactl set-sink-volume @DEFAULT_SINK@ {value}%')
    assert ok('notify-send {value}')
    assert ok('echo "fixed text" {value} \'more\'')
    assert ok('echo it\\\'s {value}')
    assert not ok('notify-send "{value}"')
    assert not ok("notify-send '{value}'")
    assert not ok('sh -c "echo {value}"')


# --- describe ----------------------------------------------------------------

def test_power_is_a_capability_of_its_own(ssh):
    told = drv.describe(DEVICE, cfg())                      # saved before power existed
    assert list(told) == ['ssh', 'power']
    assert told['power']['actions'] == {
        'restart': {'help': 'sudo -n shutdown -r +1', 'example': ''},
        'shutdown': {'help': 'sudo -n shutdown -h +1', 'example': ''}}
    assert 'restart' not in told['ssh']['actions']

    drv.run(DEVICE, 'power', 'restart', 'ignored; rm -rf /', cfg(), Secrets(), None)
    assert ssh.calls[-1].cmd[-1] == 'sudo -n shutdown -r +1'          # her value never reaches it
    drv.run(DEVICE, 'power', 'shutdown', '', cfg(shutdown_command='systemctl poweroff'), Secrets(), None)
    assert ssh.calls[-1].cmd[-1] == 'systemctl poweroff'              # the user's own words

    mine = cfg(restart_command='', shutdown_command='  ')            # the user took both away
    assert list(drv.describe(DEVICE, mine)) == ['ssh']
    assert drv.run(DEVICE, 'power', 'restart', '', mine, Secrets(), None) == \
        ('No restart command is set for this machine.', False)
    assert drv.run(DEVICE, 'power', 'explode', '', cfg(), Secrets(), None)[1] is False
    assert len(ssh.calls) == 2


def test_describe_lists_premade_and_gates_run():
    d = drv.describe(DEVICE, cfg())['ssh']
    assert d['label'] == 'SSH' and list(d['actions']) == ['close_firefox', 'volume']
    assert d['actions']['close_firefox'] == {'help': 'pkill firefox', 'example': '', 'values': ''}
    assert d['actions']['volume']['example'] == d['actions']['volume']['values'] == '<value>'
    assert list(drv.describe(DEVICE, cfg(allow_all=True))['ssh']['actions'])[-1] == 'run'


# --- run ---------------------------------------------------------------------

def test_premade_runs_as_written(ssh):
    text, ok = drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(), Secrets(), None)
    assert ok and ssh.calls[0].cmd[-2:] == ['krem@tower', 'pkill firefox']
    assert ssh.calls[0].kw['timeout'] == 30


def test_value_is_always_quoted(ssh):
    drv.run(DEVICE, 'ssh', 'volume', '40', cfg(), Secrets(), None)
    assert ssh.calls[0].cmd[-1] == 'pactl set-sink-volume @DEFAULT_SINK@ 40%'
    drv.run(DEVICE, 'ssh', 'volume', '40; rm -rf ~ $(reboot) `id`', cfg(), Secrets(), None)
    assert ssh.calls[1].cmd[-1] == ("pactl set-sink-volume @DEFAULT_SINK@ "
                                    "'40; rm -rf ~ $(reboot) `id`'%")
    drv.run(DEVICE, 'ssh', 'volume', "it's", cfg(), Secrets(), None)
    assert ssh.calls[2].cmd[-1].endswith("""'it'"'"'s'%""")


def test_a_command_without_a_slot_ignores_her_value(ssh):
    drv.run(DEVICE, 'ssh', 'close_firefox', '; reboot', cfg(), Secrets(), None)
    assert ssh.calls[0].cmd[-1] == 'pkill firefox'


def test_a_slot_needs_a_value(ssh):
    text, ok = drv.run(DEVICE, 'ssh', 'volume', '  ', cfg(), Secrets(), None)
    assert not ok and text == "'volume' needs a value." and ssh.calls == []


def test_free_form_is_gated_and_blacklisted(ssh):
    text, ok = drv.run(DEVICE, 'ssh', 'run', 'uptime', cfg(), Secrets(), None)
    assert not ok and 'Free-form commands are off' in text and ssh.calls == []
    on = cfg(allow_all=True)
    text, ok = drv.run(DEVICE, 'ssh', 'run', '', on, Secrets(), None)
    assert not ok and ssh.calls == []
    text, ok = drv.run(DEVICE, 'ssh', 'run', 'rm -rf / --no-preserve-root', on, Secrets(), None)
    assert not ok and 'blocked by safety filter' in text and ssh.calls == []
    text, ok = drv.run(DEVICE, 'ssh', 'run', 'uptime', on, Secrets(), None)
    assert ok and ssh.calls[0].cmd[-1] == 'uptime'


def test_premade_skips_the_blacklist(ssh):
    """The user's own words. Krem may well want a premade 'mkfs' some day."""
    mine = cfg(commands=[{'name': 'wipe_scratch', 'command': 'mkfs.ext4 /dev/scratch'}])
    text, ok = drv.run(DEVICE, 'ssh', 'wipe_scratch', '', mine, Secrets(), None)
    assert ok and ssh.calls[0].cmd[-1] == 'mkfs.ext4 /dev/scratch'


def test_timeout_respects_the_plugin_cap(ssh):
    with patch.object(ssh_tool, '_get_ssh_settings', lambda: {'max_timeout': 12}):
        drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(), Secrets(), None)
    assert ssh.calls[0].kw['timeout'] == 12


# --- logins ------------------------------------------------------------------

def test_four_logins(ssh):
    drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(), Secrets(), None)
    assert '-i' not in ssh.calls[0].cmd and 'BatchMode=yes' in ssh.calls[0].cmd

    drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(auth='key_file', key_path='/keys/id'), Secrets(), None)
    assert ssh.calls[1].cmd[ssh.calls[1].cmd.index('-i') + 1] == '/keys/id'

    drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(auth='key_paste'),
            Secrets(private_key='-----BEGIN KEY-----\nAAAA\n-----END KEY-----'), None)
    assert 'IdentitiesOnly=yes' in ssh.calls[2].cmd

    drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(auth='password'),
            Secrets(password='hunter2-long'), None)
    assert ssh.calls[3].env['SSH_ASKPASS_REQUIRE'] == 'force'
    assert leftovers(ssh.tmp) == []


def test_a_missing_secret_says_what_to_do(ssh):
    text, ok = drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(auth='password'), Secrets(), None)
    assert not ok and text == "No password is stored. Enter one in Settings > Devices."
    text, ok = drv.run(DEVICE, 'ssh', 'close_firefox', '', cfg(auth='key_paste'), Secrets(), None)
    assert not ok and 'No private key is stored' in text
    assert drv.status(DEVICE, cfg(auth='password'), Secrets())['online'] is False
    assert ssh.calls == []


def test_a_locked_key_is_named_plainly(ssh):
    pem = "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nAAAA\n-----END RSA PRIVATE KEY-----"
    st = drv.status(DEVICE, cfg(auth='key_paste'), Secrets(private_key=pem))
    assert st['online'] is False and 'passphrase' in st['detail'] and ssh.calls == []
    import base64
    blob = base64.b64encode(b'openssh-key-v1\x00\x00\x00\x00\naes256-ctr\x00\x00\x00\x06bcrypt' + b'x' * 40).decode()
    locked = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{blob}\n-----END OPENSSH PRIVATE KEY-----"
    assert drv._needs_passphrase(locked) is True
    plain = base64.b64encode(b'openssh-key-v1\x00\x00\x00\x00\x04none\x00\x00\x00\x04none' + b'x' * 40).decode()
    assert drv._needs_passphrase(f"-----BEGIN OPENSSH PRIVATE KEY-----\n{plain}\n-----END OPENSSH PRIVATE KEY-----") is False


# --- status ------------------------------------------------------------------

def test_status_online_and_offline(ssh):
    st = drv.status(DEVICE, cfg(), Secrets())
    assert st == {'online': True, 'detail': 'krem@tower:22'}
    assert ssh.calls[0].cmd[-1] == 'echo ok' and ssh.calls[0].kw['timeout'] == 10

    def refused(cmd, **kw):
        return SimpleNamespace(stdout='', returncode=255,
                               stderr='ssh: connect to host tower port 22: Connection refused\n')
    with patch.object(ssh_tool.subprocess, 'run', refused):
        st = drv.status(DEVICE, cfg(), Secrets())
    assert st == {'online': False,
                  'detail': 'krem@tower:22 - ssh: connect to host tower port 22: Connection refused'}


# --- the manifest ------------------------------------------------------------

def test_manifest_declares_the_driver():
    import json
    from core.devices import registry as reg
    import importlib
    importlib.reload(reg)
    manifest = json.loads((Path(drv.__file__).parent / 'plugin.json').read_text(encoding='utf-8'))
    decl = manifest['capabilities']['devices'][0]
    assert reg.register_driver(decl['driver'], decl, 'ssh')
    spec = reg.get_driver('ssh')
    assert spec['module'] == 'device_driver.py' and spec['capabilities'] == ['ssh', 'power', 'storage']
    assert spec['locked_by_default'] == ['power']           # she may not restart a machine until the user says so
    secret = [f['key'] for f in spec['config_schema'] if f.get('secret')]
    assert secret == ['private_key', 'password']
    assert (Path(drv.__file__).parent / spec['module']).exists()
    importlib.reload(reg)


# --- storage: a folder on the machine as a backup target (2026-10-06) --------

def test_copy_remote_builds_scp_with_the_same_login(ssh):
    server = {'name': 's', 'host': 'h', 'user': 'u', 'port': 2200, 'key_path': '/keys/id'}
    text, ok = ssh_tool._copy_remote(server, '/tmp/a.sapphirebak', 'backups/a.sapphirebak.partial', 60)
    assert ok and ssh.calls[0].cmd == [
        'scp', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=5',
        '-o', 'BatchMode=yes', '-P', '2200', '-i', '/keys/id',
        '-q', '/tmp/a.sapphirebak', 'u@h:backups/a.sapphirebak.partial']
    ssh_tool._copy_remote(server, 'backups/a.sapphirebak', '/tmp/back', 60, get=True)
    assert ssh.calls[1].cmd[-2:] == ['u@h:backups/a.sapphirebak', '/tmp/back']


def test_copy_remote_password_login_leaves_nothing_behind(ssh):
    server = {'name': 's', 'host': 'h', 'user': 'u', 'port': 22, 'key_path': ''}
    ssh_tool._copy_remote(server, '/tmp/a', 'b', 60, {'mode': 'password', 'secret': 'hunter2-long'})
    call = ssh.calls[0]
    assert call.cmd[0] == 'scp' and 'SSH_ASKPASS' in call.env and 'hunter2-long' not in ' '.join(call.cmd)
    assert leftovers(ssh.tmp) == []


def test_validate_backup_dir():
    assert drv.validate(cfg(backup_dir='backups/sapphire'))[1] == ''
    assert drv.validate(cfg(backup_dir='/srv/sapphire-backups/'))[0]['backup_dir'] == '/srv/sapphire-backups'
    for bad in ('~/backups', 'my backups', '../up', "x'y", '/a/../b'):
        assert 'plain path' in drv.validate(cfg(backup_dir=bad))[1], bad


def test_describe_offers_storage_only_with_a_folder():
    assert 'storage' not in drv.describe({'id': 'box'}, cfg())
    told = drv.describe({'id': 'box'}, cfg(backup_dir='backups/sapphire'))
    assert set(told['storage']['actions']) == {'backup', 'list'}


class _Remote:
    """A fake machine: answers the few shell lines the target speaks."""
    def __init__(self):
        self.files = {}
        self.sha = {}
        self.cmds = []

    def run(self, server, command, timeout, auth=None, raw=False):
        self.cmds.append(command)
        if 'ls -ln' in command:
            rows = ''.join(f"-rw-r--r-- 1 1000 1000 {n} Oct  6 02:20 {name}\n" for name, n in self.files.items())
            return f"total 8\ndrwxr-xr-x 2 1000 1000 4096 Oct  6 02:20 .\n{rows}__DF__\n/dev/sda1 100000000 50000000 20480000 50% /\n", True
        if 'sha256sum' in command:
            part = [w for w in command.split() if w.endswith('.partial')][0]
            return self.sha.get(part, 'deadbeef') + '\n', True
        if command.startswith('mv -f'):
            src, dst = command.split()[2], command.split()[3]
            self.files[dst.rsplit('/', 1)[-1]] = self.files.pop(src.rsplit('/', 1)[-1])
            return '', True
        if command.startswith('rm -f'):
            self.files.pop(command.split()[2].rsplit('/', 1)[-1], None)
            return '', True
        return '', True

    def copy(self, server, src, dst, timeout, auth=None, get=False):
        import hashlib
        data = open(src, 'rb').read()
        self.files[str(dst).rsplit('/', 1)[-1]] = len(data)
        self.sha[str(dst)] = hashlib.sha256(data).hexdigest()
        return 'copied', True


@pytest.fixture
def remote():
    r = _Remote()
    with patch.object(ssh_tool, '_run_remote', r.run), patch.object(ssh_tool, '_copy_remote', r.copy):
        yield r


def _target(**over):
    return drv.storage_target({'id': 'box', 'label': 'Den PC'}, cfg(backup_dir='backups/sapphire', **over),
                              SimpleNamespace(get=lambda k: None))


def test_target_put_verifies_sha_then_moves_into_place(remote, tmp_path):
    from core import backup_crypto
    blob = tmp_path / 'sapphire_2026-10-06_030000_daily.sapphirebak'
    blob.write_bytes(backup_crypto.MAGIC + b'\x00' * 100)
    t = _target()
    assert t.remote and t.kind == 'ssh' and t.label == 'Den PC'
    t.put(blob, blob.name)
    assert remote.files == {blob.name: 112}
    assert t.sizes() == {blob.name: 112}
    assert any(c.startswith('mv -f backups/sapphire/') for c in remote.cmds)
    info = t.info()
    assert info['free_bytes'] == 20480000 * 1024 and info['path'] == 'backups/sapphire'
    t.delete(blob.name)
    assert remote.files == {}


def test_target_put_drops_a_corrupt_arrival(remote, tmp_path):
    from core import backup_crypto
    blob = tmp_path / 'sapphire_2026-10-06_030000_daily.sapphirebak'
    blob.write_bytes(backup_crypto.MAGIC + b'\x00' * 10)
    remote.copy = lambda server, src, dst, timeout, auth=None, get=False: (
        remote.files.__setitem__(str(dst).rsplit('/', 1)[-1], 22) or ('copied', True))   # no sha recorded → deadbeef
    with patch.object(ssh_tool, '_copy_remote', remote.copy):
        with pytest.raises(drv.Problem, match='corrupt'):
            _target().put(blob, blob.name)
    assert remote.files == {}


def test_target_refuses_plaintext_and_bad_names(remote, tmp_path):
    from core.backup import BackupRefused
    plain = tmp_path / 'sapphire_2026-10-06_030000_daily.tar.gz'
    plain.write_bytes(b'x' * 10)
    with pytest.raises(BackupRefused):
        _target().put(plain, plain.name)
    with pytest.raises(drv.Problem):
        _target().delete('../etc/passwd')
    assert remote.files == {}


def test_storage_target_is_none_without_a_folder():
    assert drv.storage_target({'id': 'box'}, cfg(), SimpleNamespace(get=lambda k: None)) is None
