"""core/launcher_llm.py: the Sapphire Launcher's LLM is found (file first, then
the port) and added once to Auto (first on a fresh install, last after setup),
never over an existing setup. Plus the seams the 2026-11-08 hunt closed:
provider maps persist as the user's own fields only, a page's order merges
with the stored one, Auto names a server that answered with no model yet,
and the settings merge's lmstudio fixes (managed pop, LMSTUDIO_BASE_URL)."""
import copy
import json
import urllib.error

import pytest

from core import launcher_llm as L


class FakeSettings:
    def __init__(self, custom=None, core=None, order=None, marker=False, managed=False,
                 docker=False, complete=False, corrupt=None):
        self.d = {'LLM_CUSTOM_PROVIDERS': custom or {}, 'LLM_PROVIDERS': core or {},
                  'LLM_FALLBACK_ORDER': order or [], L.MARKER: marker, 'SETUP_COMPLETE': complete}
        self.managed, self.docker, self.corrupt_error = managed, docker, corrupt
        self.saves = 0

    def get(self, k, default=None):
        return copy.deepcopy(self.d.get(k, default))

    def set(self, k, v, persist=False):
        self.d[k] = v
        self.saves += bool(persist)

    def set_many(self, d, persist=False):
        self.d.update(d)
        self.saves += bool(persist)

    def is_managed(self):
        return self.managed

    def is_docker(self):
        return self.docker


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """No contract file anywhere, port probe answers llama.cpp; no events."""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setattr(L, '_run_file', lambda: tmp_path / 'xdg' / 'sapphire' / 'llm-run.json')
    monkeypatch.setattr(L, '_models', lambda url: ['qwen3.5-4b'])
    monkeypatch.setattr(L, '_loading', lambda url: False)
    import core.event_bus as eb
    monkeypatch.setattr(eb, 'publish', lambda *a, **k: None)

    def use(**kw):
        s = FakeSettings(**kw)
        import core.settings_manager as sm
        monkeypatch.setattr(sm, 'settings', s)
        return s
    return use


def _file(tmp_path, body):
    f = tmp_path / 'xdg' / 'sapphire' / 'llm-run.json'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body if isinstance(body, str) else json.dumps(body))


# ── finding it ──

def test_the_contract_file_wins_over_the_port(fake, tmp_path, monkeypatch):
    _file(tmp_path, {'base_url': 'http://127.0.0.1:9000', 'alias': 'x', 'pid': 1})
    seen = []
    monkeypatch.setattr(L, '_models', lambda url: seen.append(url) or ['x'])
    assert L.find() == 'http://127.0.0.1:9000/v1'
    assert seen == ['http://127.0.0.1:9000/v1']        # the default port was never probed


def test_a_left_behind_file_is_not_trusted(fake, tmp_path, monkeypatch):
    """A crash or reboot leaves the file; no llama.cpp there = no file."""
    _file(tmp_path, {'base_url': 'http://127.0.0.1:9000/v1'})
    monkeypatch.setattr(L, '_models', lambda url: None)
    assert L.find() is None


def test_a_file_naming_another_box_is_never_trusted(fake, tmp_path, monkeypatch):
    """The provider is marked local: a remote URL would open private chats to it."""
    _file(tmp_path, {'base_url': 'http://203.0.113.9:8642/v1'})
    seen = []
    monkeypatch.setattr(L, '_models', lambda url: seen.append(url) or ['x'])
    assert L.find() == L.DEFAULT_URL and L.DEFAULT_URL in seen and len(seen) == 1


def test_a_bad_file_falls_back_to_the_port(fake, tmp_path):
    _file(tmp_path, '{"base_url": 42}')
    assert L.find() == L.DEFAULT_URL


def test_nothing_llamacpp_on_the_port_is_no_launcher(fake, monkeypatch):
    monkeypatch.setattr(L, '_models', lambda url: None)
    assert L.find() is None
    s = fake()
    assert L.adopt() is None and L.KEY not in s.d['LLM_CUSTOM_PROVIDERS']


def test_a_loading_server_still_counts(fake, monkeypatch):
    monkeypatch.setattr(L, '_models', lambda url: None)
    monkeypatch.setattr(L, '_loading', lambda url: True)
    assert L.probe(L.DEFAULT_URL) == 'loading' and L.find() == L.DEFAULT_URL


class _Err:
    def __init__(self, code, body):
        self.code, self.body = code, body

    def __call__(self, url, timeout):
        raise urllib.error.HTTPError(url, self.code, 'x', {}, _Body(self.body))


class _Body:
    def __init__(self, b):
        self.b = b.encode()

    def read(self, *a):
        return self.b

    def close(self):
        pass


@pytest.mark.parametrize('code,body,want', [
    (503, '{"error":{"code":503,"message":"Loading model"}}', True),
    (503, 'Service Unavailable', False),       # some other server, mid-restart
    (404, 'not found', False),
])
def test_only_llamacpp_loading_counts_as_loading(monkeypatch, code, body, want):
    monkeypatch.setattr(L._opener, 'open', _Err(code, body))
    assert L._loading('http://127.0.0.1:8642/v1') is want


def test_windows_path_is_capital_s_with_the_userprofile_fallback(monkeypatch):
    from core.launcher_llm import _run_file as real
    monkeypatch.setenv('APPDATA', '/w/Roaming')
    assert real(windows=True).parts[-2:] == ('Sapphire', 'llm-run.json')
    monkeypatch.delenv('APPDATA')
    monkeypatch.setenv('USERPROFILE', '/w/me')
    assert real(windows=True).as_posix() == '/w/me/AppData/Roaming/Sapphire/llm-run.json'


def test_linux_path_is_lowercase_under_xdg(monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', '/x')
    assert L._run_file(windows=False).as_posix() == '/x/sapphire/llm-run.json'


# ── adding it ──

def test_a_fresh_install_gets_it_first_in_auto_in_one_save(fake):
    s = fake(custom={'lmstudio': {'base_url': 'http://127.0.0.1:1234/v1'}}, order=['lmstudio', 'claude'])
    assert L.adopt() == L.KEY
    p = s.d['LLM_CUSTOM_PROVIDERS'][L.KEY]
    assert p['base_url'] == L.DEFAULT_URL and p['enabled'] and p['is_local'] and p['model'] == ''
    assert p['disable_thinking'] is True       # small launcher models loop in thinking (2026-11-08)
    assert s.d['LLM_FALLBACK_ORDER'] == [L.KEY, 'lmstudio', 'claude']
    assert s.d[L.MARKER] is True and s.saves == 1
    assert 'lmstudio' in s.d['LLM_CUSTOM_PROVIDERS']


def test_a_finished_setup_gets_it_last_so_no_brain_changes(fake):
    s = fake(order=['claude', 'lmstudio'], complete=True)
    assert L.adopt() == L.KEY
    assert s.d['LLM_FALLBACK_ORDER'] == ['claude', 'lmstudio', L.KEY]


def test_deleted_stays_deleted(fake):
    s = fake(marker=True)
    assert L.adopt() is None and L.KEY not in s.d['LLM_CUSTOM_PROVIDERS']


def test_an_unreadable_settings_file_adds_nothing(fake):
    s = fake(corrupt='settings.json is unreadable')
    assert L.adopt() is None and s.saves == 0


@pytest.mark.parametrize('url', ['http://localhost:8642/v1', 'http://127.0.0.1:8642',
                                 'HTTP://0.0.0.0:8642/v1/', 'http://[::1]:8642/v1', 'http://127.1:8642'])
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


def test_same_url_is_not_fooled():
    assert L.same_url('http://localhost:8642', 'http://127.0.0.1:8642/v1/')
    assert not L.same_url('http://127.0.0.1:8643/v1', 'http://127.0.0.1:8642/v1')
    assert not L.same_url('http://127.0.0.1.evil.com:8642', 'http://127.0.0.1:8642')
    assert not L.same_url('', 'http://127.0.0.1:8642/v1')


# ── the banner's status ──

def test_status_names_the_state_and_survives_odd_props(fake, monkeypatch):
    fake(custom={L.KEY: {'base_url': L.DEFAULT_URL, 'enabled': True}}, order=[L.KEY])
    monkeypatch.setattr(L, '_get', lambda url: {'default_generation_settings': [1], 'n_ctx': True})
    s = L.status()
    assert s['state'] == 'running' and s['model'] == 'qwen3.5-4b' and s['n_ctx'] is None and s['first_in_auto']
    monkeypatch.setattr(L, '_models', lambda url: None)
    monkeypatch.setattr(L, '_loading', lambda url: True)
    assert L.status()['state'] == 'loading'
    monkeypatch.setattr(L, '_loading', lambda url: False)
    assert L.status()['state'] == 'stopped'


# ── the order a page sends merges with the stored one ──

def test_a_stale_page_order_keeps_the_provider_it_never_saw():
    from core.chat.llm_providers import merge_order
    providers = dict.fromkeys(['sapphire_launcher', 'claude', 'lmstudio', 'gemini'], {})
    stored = ['sapphire_launcher', 'claude', 'lmstudio', 'gemini']
    assert merge_order(['gemini', 'claude', 'lmstudio'], stored, providers) == \
        ['sapphire_launcher', 'gemini', 'claude', 'lmstudio']
    assert merge_order(['lmstudio', 'claude'], ['claude', 'new', 'lmstudio'],
                       dict.fromkeys(['claude', 'new', 'lmstudio'], {})) == ['lmstudio', 'claude', 'new']
    assert merge_order(['claude'], ['ghost', 'claude'], {'claude': {}}) == ['claude']


# ── provider maps persist as the user's own fields only ──

def _sm(monkeypatch, env=None):
    from core.settings_manager import SettingsManager
    for k in ('SAPPHIRE_MANAGED', 'LMSTUDIO_BASE_URL'):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    sm = SettingsManager.__new__(SettingsManager)
    stock = {'template': 'openai', 'base_url': 'http://127.0.0.1:1234/v1', 'enabled': True}
    sm._defaults = {'LLM_PROVIDERS': {'claude': {'model': 'claude-opus-5-5', 'enabled': False}},
                    'LLM_CUSTOM_PROVIDERS': {'lmstudio': stock}}
    sm._user, sm._runtime = {}, {}
    return sm, stock


def test_stock_values_are_never_frozen_into_the_user_file(monkeypatch):
    sm, _ = _sm(monkeypatch, {'LMSTUDIO_BASE_URL': 'http://host.docker.internal:1234/v1'})
    merged = {'lmstudio': {'template': 'openai', 'base_url': 'http://host.docker.internal:1234/v1',
                           'enabled': False},
              'mine': {'template': 'openai', 'base_url': 'http://x/v1'}}
    assert sm._persisted('LLM_CUSTOM_PROVIDERS', merged) == {
        'lmstudio': {'enabled': False},          # the user's one change; env URL and stock stay out
        'mine': merged['mine']}                  # a provider stock doesn't know is whole
    assert sm._persisted('LLM_PROVIDERS', {'claude': {'model': 'claude-opus-5-5', 'enabled': True}}) == \
        {'claude': {'enabled': True}}
    assert sm._persisted('LLM_FALLBACK_ORDER', ['a']) == ['a']


def test_a_brain_the_file_already_holds_is_never_taken_back(monkeypatch):
    # thinned, the next release's stock change moved that person's model
    sm, _ = _sm(monkeypatch)
    # someone who picked another model, then went back to the stock one
    sm._user = {'LLM_PROVIDERS': {'claude': {'model': 'claude-sonnet-5-5'}}}
    back = {'claude': {'model': 'claude-opus-5-5', 'enabled': False, 'max_tokens': 4096}}
    assert sm._persisted('LLM_PROVIDERS', back) == {'claude': {'model': 'claude-opus-5-5', 'max_tokens': 4096}}
    # an install whose file predates thinning: the whole map, frozen
    sm._user = {'LLM_PROVIDERS': {'claude': {'model': 'claude-opus-5-5', 'enabled': False, 'timeout': 5}}}
    sm._defaults['LLM_PROVIDERS']['claude']['timeout'] = 5
    assert sm._persisted('LLM_PROVIDERS', {'claude': {'model': 'claude-opus-5-5', 'enabled': False, 'timeout': 5}}) \
        == {'claude': {'model': 'claude-opus-5-5', 'enabled': False}}      # the brain stays; the rest follows stock
    # the env's lmstudio URL is still never the user's
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://host.docker.internal:1234/v1')
    sm._user = {'LLM_CUSTOM_PROVIDERS': {'lmstudio': {'base_url': 'http://host.docker.internal:1234/v1'}}}
    assert sm._persisted('LLM_CUSTOM_PROVIDERS', {'lmstudio': {
        'template': 'openai', 'base_url': 'http://host.docker.internal:1234/v1', 'enabled': True}}) == {'lmstudio': {}}


# ── Auto's words when the brain is up but its model is not ready ──

def test_auto_names_the_server_that_answered_without_a_model(monkeypatch):
    from core.chat.llm_providers import resolve
    monkeypatch.setattr(resolve, 'display_name', lambda k: {'sapphire_launcher': 'Sapphire Launcher',
                                                            'lmstudio': 'LM Studio'}[k])
    words = resolve.auto_words([('sapphire_launcher', 'has no model set, and its server named none'),
                                ('lmstudio', 'not answering (ConnectError, 0.3s)')])
    assert words.startswith('Sapphire Launcher answered') and 'LM Studio' not in words


# ── the settings merge's lmstudio fixes ──

def _merged(monkeypatch, env):
    sm, stock = _sm(monkeypatch, env)
    sm._merge_settings()
    return sm, stock


def test_lmstudio_base_url_env_reaches_the_custom_provider(monkeypatch):
    sm, stock = _merged(monkeypatch, {'LMSTUDIO_BASE_URL': 'http://host.docker.internal:1234/v1'})
    assert sm._config['LLM_CUSTOM_PROVIDERS']['lmstudio']['base_url'] == 'http://host.docker.internal:1234/v1'
    assert stock['base_url'] == 'http://127.0.0.1:1234/v1'      # the stock defaults dict is untouched


def test_managed_mode_really_drops_lmstudio(monkeypatch):
    sm, _ = _merged(monkeypatch, {'SAPPHIRE_MANAGED': '1'})
    assert 'lmstudio' not in sm._config['LLM_CUSTOM_PROVIDERS']


# ── the provider follows the launcher's port (it can be set) ──

def test_the_provider_follows_the_contract_files_port(fake, tmp_path):
    assert L.live_url('http://127.0.0.1:8642/v1') == 'http://127.0.0.1:8642/v1'      # no file: stored URL
    _file(tmp_path, {'base_url': 'http://127.0.0.1:9100/v1'})
    assert L.live_url('http://127.0.0.1:8642/v1') == 'http://127.0.0.1:9100/v1'
    import os, time
    f = tmp_path / 'xdg' / 'sapphire' / 'llm-run.json'
    f.write_text(json.dumps({'base_url': 'http://203.0.113.9:9200/v1'}))
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert L.live_url('http://127.0.0.1:8642/v1') == 'http://127.0.0.1:8642/v1'      # never a remote box


def test_the_registry_builds_the_launcher_at_the_live_port(fake, tmp_path):
    from core.chat.llm_providers import provider_registry as R
    _file(tmp_path, {'base_url': 'http://127.0.0.1:9101/v1'})
    conf = {L.KEY: {'template': 'openai', 'base_url': 'http://127.0.0.1:8642/v1', 'enabled': True, 'is_local': True},
            'other': {'template': 'openai', 'base_url': 'http://127.0.0.1:8642/v1', 'enabled': True, 'is_local': True}}
    assert R.get_provider_by_key(L.KEY, conf).base_url.startswith('http://127.0.0.1:9101')
    assert R.get_provider_by_key('other', conf).base_url.startswith('http://127.0.0.1:8642')


# ── Auto with no brain looks for the launcher once more (hunt 2026-11-13) ──

def test_auto_with_no_brain_adopts_the_launcher_and_looks_again(monkeypatch):
    # boot came before llama-server and the wizard's Brain step was passed:
    # nothing looked again, and every turn said "No LLM" until a restart
    from core.chat.llm_providers import resolve as R
    seen = []

    class _LP:
        @staticmethod
        def get_first_available_provider(cfg, order, timeout, **kw):
            seen.append((sorted(cfg), list(order)))
            return ('sapphire_launcher', type('P', (), {'model': 'qwen'})()) if 'sapphire_launcher' in cfg else None

    monkeypatch.setattr(R, '_lp', lambda: _LP)
    monkeypatch.setattr(R, 'providers_config', lambda: {'lmstudio': {}, 'sapphire_launcher': {}})
    adopts = []
    monkeypatch.setattr(L, 'adopt', lambda: adopts.append(1) or L.KEY)
    sel = R._auto({'lmstudio': {}}, ['lmstudio'], False, 240.0, 'cached', False)
    assert sel.key == 'sapphire_launcher' and adopts == [1]
    assert seen[1] == (['lmstudio', 'sapphire_launcher'], ['lmstudio', 'sapphire_launcher'])


def test_auto_with_no_brain_and_no_launcher_says_so_once(monkeypatch):
    from core.chat.llm_providers import resolve as R
    calls = []

    class _LP:
        @staticmethod
        def get_first_available_provider(cfg, order, timeout, **kw):
            calls.append(1)
            return None

    monkeypatch.setattr(R, '_lp', lambda: _LP)
    monkeypatch.setattr(L, 'adopt', lambda: None)
    with pytest.raises(R.NoProvidersAvailable):
        R._auto({'lmstudio': {}}, ['lmstudio'], False, 240.0, 'cached', False)
    assert calls == [1]                                 # no second scan without a new provider


def test_auto_that_found_a_brain_never_probes_for_the_launcher(monkeypatch):
    from core.chat.llm_providers import resolve as R

    class _LP:
        @staticmethod
        def get_first_available_provider(cfg, order, timeout, **kw):
            return ('claude', type('P', (), {'model': 'm'})())

    monkeypatch.setattr(R, '_lp', lambda: _LP)
    monkeypatch.setattr(L, 'adopt', lambda: (_ for _ in ()).throw(AssertionError('probed')))
    assert R._auto({'claude': {}}, ['claude'], False, 240.0, 'cached', False).key == 'claude'
