"""core/launcher_llm.py: the Sapphire Launcher's LLM is found (file first, then
the port) and added once, at the top of Auto, never over an existing setup.
Plus the settings merge's lmstudio fixes (managed pop, LMSTUDIO_BASE_URL)."""
import copy
import json

import pytest

from core import launcher_llm as L


class FakeSettings:
    def __init__(self, custom=None, core=None, order=None, marker=False, managed=False, docker=False):
        self.d = {'LLM_CUSTOM_PROVIDERS': custom or {}, 'LLM_PROVIDERS': core or {},
                  'LLM_FALLBACK_ORDER': order or [], L.MARKER: marker}
        self.managed, self.docker = managed, docker

    def get(self, k, default=None):
        return copy.deepcopy(self.d.get(k, default))

    def set(self, k, v, persist=False):
        self.d[k] = v

    def is_managed(self):
        return self.managed

    def is_docker(self):
        return self.docker


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """No contract file anywhere, port probe answers llama.cpp; no events."""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.delenv('APPDATA', raising=False)
    monkeypatch.setattr(L.Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(L, '_models', lambda url: ['qwen3.5-4b'])
    import core.event_bus as eb
    monkeypatch.setattr(eb, 'publish', lambda *a, **k: None)

    def use(**kw):
        s = FakeSettings(**kw)
        import core.settings_manager as sm
        monkeypatch.setattr(sm, 'settings', s)
        return s
    return use


def test_the_contract_file_wins_over_the_port(fake, tmp_path, monkeypatch):
    f = tmp_path / 'xdg' / 'sapphire' / 'llm-run.json'
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({'base_url': 'http://127.0.0.1:9000', 'alias': 'x', 'pid': 1}))
    monkeypatch.setattr(L, '_alive', lambda url: True)
    monkeypatch.setattr(L, '_models', lambda url: pytest.fail('probed despite the file'))
    assert L.find() == 'http://127.0.0.1:9000/v1'


def test_a_left_behind_file_is_not_trusted(fake, tmp_path, monkeypatch):
    """A crash or reboot leaves the file; its server not answering = no file."""
    f = tmp_path / 'xdg' / 'sapphire' / 'llm-run.json'
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({'base_url': 'http://127.0.0.1:9000/v1'}))
    monkeypatch.setattr(L, '_alive', lambda url: False)
    monkeypatch.setattr(L, '_models', lambda url: None)
    assert L.find() is None


def test_windows_path_is_capital_s_with_the_userprofile_fallback(monkeypatch):
    monkeypatch.setenv('APPDATA', '/w/Roaming')
    assert L._run_file(windows=True).parts[-2:] == ('Sapphire', 'llm-run.json')
    monkeypatch.delenv('APPDATA')
    monkeypatch.setenv('USERPROFILE', '/w/me')
    assert L._run_file(windows=True).as_posix() == '/w/me/AppData/Roaming/Sapphire/llm-run.json'


def test_linux_path_is_lowercase_under_xdg(monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', '/x')
    assert L._run_file(windows=False).as_posix() == '/x/sapphire/llm-run.json'


def test_a_loading_server_503_still_counts_as_alive(monkeypatch):
    import urllib.error
    def boom(url, timeout):
        raise urllib.error.HTTPError(url, 503, 'loading', {}, None)
    monkeypatch.setattr(L._opener, 'open', boom)
    assert L._alive('http://127.0.0.1:8642/v1')


def test_a_bad_file_falls_back_to_the_port(fake, tmp_path):
    f = tmp_path / 'xdg' / 'sapphire' / 'llm-run.json'
    f.parent.mkdir(parents=True)
    f.write_text('{"base_url": 42}')
    assert L.find() == L.DEFAULT_URL


def test_nothing_llamacpp_on_the_port_is_no_launcher(fake, monkeypatch):
    monkeypatch.setattr(L, '_models', lambda url: None)
    assert L.find() is None
    s = fake()
    assert L.adopt() is None and L.KEY not in s.d['LLM_CUSTOM_PROVIDERS']


def test_adopt_adds_it_first_in_auto_once(fake):
    s = fake(custom={'lmstudio': {'base_url': 'http://127.0.0.1:1234/v1'}}, order=['lmstudio', 'claude'])
    assert L.adopt() == L.KEY
    p = s.d['LLM_CUSTOM_PROVIDERS'][L.KEY]
    assert p['base_url'] == L.DEFAULT_URL and p['enabled'] and p['is_local'] and p['model'] == ''
    assert s.d['LLM_FALLBACK_ORDER'] == [L.KEY, 'lmstudio', 'claude']
    assert s.d[L.MARKER] is True
    assert 'lmstudio' in s.d['LLM_CUSTOM_PROVIDERS']


def test_deleted_stays_deleted(fake):
    s = fake(marker=True)
    assert L.adopt() is None and L.KEY not in s.d['LLM_CUSTOM_PROVIDERS']


@pytest.mark.parametrize('url', ['http://localhost:8642/v1', 'http://127.0.0.1:8642',
                                 'HTTP://0.0.0.0:8642/v1/'])
def test_never_over_a_provider_already_at_that_url(fake, url):
    s = fake(custom={'mine': {'base_url': url}}, order=['mine'])
    assert L.adopt() is None
    assert list(s.d['LLM_CUSTOM_PROVIDERS']) == ['mine'] and s.d['LLM_FALLBACK_ORDER'] == ['mine']
    assert s.d[L.MARKER] is False


def test_a_taken_key_is_never_overwritten(fake):
    s = fake(custom={L.KEY: {'base_url': 'http://10.0.0.5:8080/v1'}})
    assert L.adopt() is None
    assert s.d['LLM_CUSTOM_PROVIDERS'][L.KEY]['base_url'] == 'http://10.0.0.5:8080/v1'


@pytest.mark.parametrize('kw', [{'managed': True}, {'docker': True}])
def test_not_in_managed_or_docker(fake, kw):
    s = fake(**kw)
    assert L.adopt() is None and not s.d['LLM_CUSTOM_PROVIDERS']


def test_same_url_is_not_fooled_by_another_port():
    assert L.same_url('http://localhost:8642', 'http://127.0.0.1:8642/v1/')
    assert not L.same_url('http://127.0.0.1:8643/v1', 'http://127.0.0.1:8642/v1')
    assert not L.same_url('', 'http://127.0.0.1:8642/v1')


def _merged(monkeypatch, env):
    from core.settings_manager import SettingsManager
    for k in ('SAPPHIRE_MANAGED', 'LMSTUDIO_BASE_URL'):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sm = SettingsManager.__new__(SettingsManager)
    stock = {'template': 'openai', 'base_url': 'http://127.0.0.1:1234/v1'}
    sm._defaults = {'LLM_PROVIDERS': {}, 'LLM_CUSTOM_PROVIDERS': {'lmstudio': stock}}
    sm._user = {}
    sm._runtime = {}
    sm._merge_settings()
    return sm, stock


def test_lmstudio_base_url_env_reaches_the_custom_provider(monkeypatch):
    sm, stock = _merged(monkeypatch, {'LMSTUDIO_BASE_URL': 'http://host.docker.internal:1234/v1'})
    assert sm._config['LLM_CUSTOM_PROVIDERS']['lmstudio']['base_url'] == 'http://host.docker.internal:1234/v1'
    assert stock['base_url'] == 'http://127.0.0.1:1234/v1'      # the stock defaults dict is untouched


def test_managed_mode_really_drops_lmstudio(monkeypatch):
    sm, _ = _merged(monkeypatch, {'SAPPHIRE_MANAGED': '1'})
    assert 'lmstudio' not in sm._config['LLM_CUSTOM_PROVIDERS']
