# tests/test_extras.py - optional sets installed into her environment when a
# page first needs one (core/extras.py). pip is faked; nothing is installed.

import importlib.util
import time
import types
from unittest.mock import patch

import pytest

from core import extras


@pytest.fixture(autouse=True)
def clean(tmp_path):
    extras._job = None
    with patch.object(extras, 'LOG_DIR', tmp_path / 'logs'), patch.object(extras, '_managed', lambda: False):
        yield
    extras._job = None


def present(*names):
    """find_spec that knows these modules and no others."""
    return lambda m, *a, **k: object() if m in names else None


def test_only_the_named_sets_exist_and_flash_is_one():
    assert set(extras.EXTRAS) == {'flash'}
    assert (extras.ROOT / extras.EXTRAS['flash']['file']).is_file()
    assert extras.EXTRAS['flash']['restart'] is False


def test_installed_looks_fresh_and_needs_every_module():
    with patch.object(importlib.util, 'find_spec', present('esptool', 'serial')):
        assert extras.installed('flash') is True
    with patch.object(importlib.util, 'find_spec', present('esptool')):
        assert extras.installed('flash') is False
    assert extras.installed('nope') is False
    with patch.object(importlib.util, 'find_spec', present('esptool', 'serial')):
        st = extras.status()['flash']
    assert st['installed'] is True and st['job'] is None and 'esptool' in st['note']


def test_start_refuses_what_it_should():
    with pytest.raises(extras.ExtraError, match='no optional set'):
        extras.start('torch-everything')
    with patch.object(extras, '_managed', lambda: True):
        with pytest.raises(extras.ExtraError, match='hosted'):
            extras.start('flash')
    extras._job = {'name': 'flash', 'state': 'running', 'lines': [], 'error': '', 'started': 0, 'installed': False}
    with pytest.raises(extras.ExtraError, match='being installed'):
        extras.start('flash')
    extras._job = None
    with patch.object(importlib.util, 'find_spec', present('esptool', 'serial')):
        assert extras.start('flash')['state'] == 'done'          # already there: nothing runs


class FakePip:
    def __init__(self, lines, code):
        self.stdout = iter(lines)
        self.code = code

    def wait(self, timeout=None): return self.code
    def kill(self): pass


def _run(lines, code, after_install):
    seen = {}

    def popen(cmd, **kw):
        seen['cmd'] = cmd
        seen['env'] = kw['env']
        return FakePip(lines, code)
    found = [False]
    spec = lambda m, *a, **k: object() if found[0] and m in ('esptool', 'serial') else None
    with patch.object(extras.subprocess, 'Popen', popen), patch.object(importlib.util, 'find_spec', spec):
        job = extras.start('flash')
        assert job['state'] == 'running'
        found[0] = after_install
        for _ in range(200):
            if extras.state('flash')['state'] != 'running':
                break
            time.sleep(0.01)
    return extras.state('flash'), seen


def test_a_good_install_runs_pip_on_her_own_interpreter_and_checks(tmp_path):
    st, seen = _run(['Collecting esptool\n', 'Installing collected packages: esptool\n', 'Successfully installed esptool-5.4.0\n'],
                    0, after_install=True)
    assert st['state'] == 'done' and st['installed'] is True
    assert st['lines'][-1].startswith('Successfully installed')
    assert seen['cmd'][:4] == [extras.sys.executable, '-m', 'pip', 'install']
    assert seen['cmd'][4] == '-r' and seen['cmd'][5].endswith('requirements-flash.txt') and len(seen['cmd']) == 6
    assert seen['env']['PIP_DISABLE_PIP_VERSION_CHECK'] == '1'
    assert (tmp_path / 'logs' / 'extras-flash.log').read_text().count('\n') >= 4
    assert extras.status()['flash']['job']['state'] == 'done'


def test_a_failed_install_says_so_and_names_the_log():
    st, _ = _run(['ERROR: No matching distribution found for esptool>=5.0\n'], 1, after_install=False)
    assert st['state'] == 'failed' and 'exit 1' in st['error'] and 'extras-flash.log' in st['error']


def test_pip_that_lies_is_caught():
    st, _ = _run(['Successfully installed nothing\n'], 0, after_install=False)
    assert st['state'] == 'failed' and 'still cannot be found' in st['error']
