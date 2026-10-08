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


# ── plugin sets: `plugin:<name>` built from a plugin's own manifest (2026-10-08) ──

def _loader_with(manifest, name='demo'):
    info = {'name': name, 'manifest': manifest, 'path': '/x'}
    return types.SimpleNamespace(get_plugin_info=lambda n: info if n == name else None,
                                 reload_plugin=lambda n: None)


def test_a_plugin_declares_its_set_hard_or_optional(monkeypatch):
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'plugin_loader', _loader_with({
        'title': 'Demo', 'pip_dependencies': ['telethon>=1.34'],
        'extra': {'label': 'Big thing', 'pip': ['bigthing>=2'], 'modules': ['bigthing'], 'note': 'huge'}}))
    spec = extras._spec('plugin:demo')
    assert spec['specs'] == ['telethon>=1.34', 'bigthing>=2'] and spec['hard'] is True and spec['label'] == 'Big thing'
    assert extras.known('plugin:demo') and not extras.known('plugin:ghost') and not extras.known('demo')
    with patch.object(importlib.util, 'find_spec', present('bigthing')):
        assert extras.installed('plugin:demo') is True          # `modules` proves it
        x = extras.plugin_extra('demo')
    assert x['name'] == 'plugin:demo' and x['installed'] is True and x['hard'] is True
    # optional only, no modules named: the distributions are checked
    monkeypatch.setattr(pl, 'plugin_loader', _loader_with({'extra': {'pip': ['bigthing>=2']}}))
    with patch.object(importlib.metadata, 'version', lambda d: '2.0' if d == 'bigthing' else (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError(d))):
        assert extras.installed('plugin:demo') is True
        assert extras.plugin_extra('demo')['hard'] is False
    with patch.object(importlib.metadata, 'version', lambda d: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError(d))):
        assert extras.installed('plugin:demo') is False
    # a plugin that declares nothing has no set
    monkeypatch.setattr(pl, 'plugin_loader', _loader_with({'title': 'Plain'}))
    assert extras._spec('plugin:demo') is None and extras.plugin_extra('demo') is None
    assert extras.describe('plugin:demo') is None and extras.describe('flash')['label']


def test_a_manifest_cannot_smuggle_pip_arguments(monkeypatch):
    import core.plugin_loader as pl
    monkeypatch.setattr(pl, 'plugin_loader', _loader_with({'extra': {'pip': [
        '--index-url=http://evil', '-e .', 'git+https://x/y.git', '/tmp/wheel.whl', 'good-pkg>=1.0,<2', 'other[extra]==3.1',
        'py-cord[voice] @ git+https://github.com/Pycord-Development/pycord.git@abc123']}}))
    # options, bare URLs and paths never reach pip; a PEP 508 direct reference (what
    # pip_dependencies has always allowed - the discord plugin's py-cord) does
    assert extras._spec('plugin:demo')['specs'] == ['good-pkg>=1.0,<2', 'other[extra]==3.1',
                                                   'py-cord[voice] @ git+https://github.com/Pycord-Development/pycord.git@abc123']


def test_a_plugin_set_installs_by_spec_and_reloads_the_plugin(monkeypatch, tmp_path):
    import core.plugin_loader as pl
    reloaded = []
    loader = _loader_with({'extra': {'label': 'Big', 'pip': ['bigthing>=2'], 'modules': ['bigthing']}})
    loader.reload_plugin = lambda n: reloaded.append(n)
    monkeypatch.setattr(pl, 'plugin_loader', loader)
    seen = {}

    def popen(cmd, **kw):
        seen['cmd'] = cmd
        return FakePip(['Successfully installed bigthing-2.0\n'], 0)
    found = [False]
    with patch.object(extras.subprocess, 'Popen', popen), \
         patch.object(importlib.util, 'find_spec', lambda m, *a, **k: object() if found[0] and m == 'bigthing' else None):
        job = extras.start('plugin:demo')
        assert job['state'] == 'running'
        found[0] = True
        for _ in range(200):
            if extras.state('plugin:demo')['state'] != 'running':
                break
            time.sleep(0.01)
    st = extras.state('plugin:demo')
    assert st['state'] == 'done' and seen['cmd'][-1] == 'bigthing>=2' and '-r' not in seen['cmd']
    assert reloaded == ['demo']                                  # the plugin sees its packages now
    assert (tmp_path / 'logs' / 'extras-plugin-demo.log').is_file()


def test_the_page_asks_for_a_set_by_name_so_plugin_sets_install():
    """Krem's first real click on server Sapph (2026-10-08): "Install failed: No
    optional set called 'plugin:claude-code'". ensureExtra read the list of
    CORE sets (/api/system/extras, status()) - a plugin's set is only known to
    the per-set route (known + describe). The browser threw before the server
    ever heard of the install."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / 'interfaces' / 'web' / 'static' / 'shared' / 'extras.js').read_text(encoding='utf-8')
    head = js.split('return new Promise')[0]
    assert '`/api/system/extras/${name}`' in head, 'the set is fetched by name'
    assert "fetchWithTimeout('/api/system/extras'," not in head, 'the core-set list does not decide what exists'
    assert 'st?.set' in head or 'st.set' in head
    # and the per-set route answers for a plugin's set with what the modal reads
    import core.plugin_loader as pl
    with patch.object(pl, 'plugin_loader', _loader_with({'extra': {'label': 'Big', 'pip': ['bigthing>=2'], 'modules': ['bigthing']}})):
        d = extras.describe('plugin:demo')
    assert set(d) >= {'label', 'note', 'restart', 'installed'} and d['label'] == 'Big'
