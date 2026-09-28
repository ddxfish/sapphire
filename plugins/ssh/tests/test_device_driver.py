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

def test_describe_lists_premade_and_gates_run():
    d = drv.describe(DEVICE, cfg())['ssh']
    assert d['label'] == 'SSH' and list(d['actions']) == ['close_firefox', 'volume']
    assert d['actions']['close_firefox'] == {'help': 'pkill firefox', 'example': ''}
    assert d['actions']['volume']['example'] == '<value>'
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
    assert spec['module'] == 'device_driver.py' and spec['capabilities'] == ['ssh']
    secret = [f['key'] for f in spec['config_schema'] if f.get('secret')]
    assert secret == ['private_key', 'password']
    assert (Path(drv.__file__).parent / spec['module']).exists()
    importlib.reload(reg)
