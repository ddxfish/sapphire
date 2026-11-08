"""[REGRESSION_GUARD] The server owns names (playwright hunt 2026-11-08).

- A persona rename onto a taken name refuses the WHOLE update. It used to
  save the settings, skip the rename and report success; the editor then
  chased the other persona's name and the next edit overwrote it.
- Persona rename carries DEFAULT_PERSONA and continuity tasks; delete
  clears DEFAULT_PERSONA.
- Prompt rename is one server call: moves the prompt in its own store,
  refuses a taken name, repoints chats, personas and tasks. The editor's
  save-new + delete-old overwrote same-named prompts and stranded referrers.

Hermetic: tmp persona dir, stubbed prompt savers, fake system/settings.
"""
import threading
from types import SimpleNamespace

import pytest

import core.prompt_crud as crud
import core.prompt_vault as pv
from core.prompt_manager import prompt_manager


class FakeSettings:
    def __init__(self, **kv):
        self.kv = dict(kv)

    def get(self, k, default=None):
        return self.kv.get(k, default)

    def set(self, k, v, persist=False):
        self.kv[k] = v


class FakeSched:
    def __init__(self, tasks):
        self.tasks = tasks

    def repoint_ref(self, key, old, new):
        hit = [t for t in self.tasks if t.get(key) == old]
        for t in hit:
            t[key] = new
        return [t['name'] for t in hit]


class FakeSM:
    def __init__(self, chats):
        self.chats = chats

    def reset_chat_scope_ref(self, key, old, reset_to='default'):
        hit = [n for n, s in self.chats.items() if s.get(key) == old]
        for n in hit:
            self.chats[n][key] = reset_to
        return hit


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Fake settings + system (chats, tasks) every sync helper resolves."""
    import core.settings_manager as sm_mod
    import core.api_fastapi as api
    fs = FakeSettings(DEFAULT_PERSONA='zed', PERSONA_FAVORITES=[])
    chats = {'c1': {'persona': 'zed', 'prompt': 'ronnie'}, 'c2': {'prompt': 'beach'}}
    tasks = [{'name': 't1', 'persona': 'zed', 'prompt': 'ronnie'}]
    system = SimpleNamespace(llm_chat=SimpleNamespace(session_manager=FakeSM(chats)),
                             continuity_scheduler=FakeSched(tasks))
    monkeypatch.setattr(sm_mod, 'settings', fs)
    monkeypatch.setattr(api, 'get_system', lambda: system)
    # Registry externals that would otherwise touch real user files.
    import core.api_tokens as tok
    import core.rooms as rooms
    import core.chat.history as hist
    tokens = [{'name': 'mcp-zed', 'persona': 'zed'}]
    seats = {'den': [{'id': 's1', 'kind': 'door', 'persona': 'zed'}]}

    def tok_repoint(old, new):
        hit = [t for t in tokens if t['persona'] == old]
        for t in hit:
            t['persona'] = new or None
        return [t['name'] for t in hit]

    def seat_repoint(old, new):
        hit = [r for r, ss in seats.items() if any(x['persona'] == old for x in ss)]
        for r in hit:
            for x in seats[r]:
                if x['persona'] == old:
                    x['persona'] = new
        return hit
    monkeypatch.setattr(tok.api_tokens, 'repoint_persona', tok_repoint)
    monkeypatch.setattr(rooms, 'repoint_persona', seat_repoint)
    defaults = tmp_path / 'chat_defaults.json'
    defaults.write_text('{"prompt": "ronnie", "persona": "zed"}')
    monkeypatch.setattr(hist, '_user_chat_defaults_path', lambda: defaults)
    return SimpleNamespace(settings=fs, chats=chats, tasks=tasks, tokens=tokens,
                           seats=seats, defaults=defaults)


@pytest.fixture
def mgr(tmp_path):
    from core.personas.persona_manager import PersonaManager
    m = PersonaManager.__new__(PersonaManager)
    m._lock = threading.RLock()
    m.BASE_DIR = tmp_path
    m.USER_DIR = tmp_path / "personas"
    m.USER_AVATARS = m.USER_DIR / "avatars"
    m.USER_AVATARS.mkdir(parents=True)
    m._personas = {
        'zed': {'name': 'zed', 'tagline': 'Z', 'avatar': None,
                'settings': {'prompt': 'ronnie', 'trim_color': '#111111'}},
        'amy': {'name': 'amy', 'tagline': 'A', 'avatar': None,
                'settings': {'prompt': 'beach', 'trim_color': '#222222'}},
    }
    return m


# ── personas ──

def test_rename_onto_taken_name_refuses_everything(mgr, world):
    ok = mgr.update('zed', {'name': 'Amy', 'tagline': 'CHANGED',
                            'settings': {'trim_color': '#ff0000'}})
    assert ok is False
    assert mgr._personas['zed']['tagline'] == 'Z'
    assert mgr._personas['zed']['settings']['trim_color'] == '#111111'
    assert mgr._personas['amy']['tagline'] == 'A'
    assert mgr._personas['amy']['settings']['trim_color'] == '#222222'


def test_rename_problem_words(mgr):
    assert 'already exists' in mgr.rename_problem('zed', 'amy')
    assert mgr.rename_problem('zed', '!!!')
    assert mgr.rename_problem('zed', 'Zed') is None      # sanitizes to itself
    assert mgr.rename_problem('zed', 'Zed Two') is None


def test_rename_carries_default_chats_and_tasks(mgr, world):
    assert mgr.update('zed', {'name': 'Zed Two'})
    assert 'zed_two' in mgr._personas and 'zed' not in mgr._personas
    assert world.settings.kv['DEFAULT_PERSONA'] == 'zed_two'
    assert world.chats['c1']['persona'] == 'zed_two'
    assert world.tasks[0]['persona'] == 'zed_two'


def test_delete_clears_default(mgr, world):
    assert mgr.delete('zed')
    assert world.settings.kv['DEFAULT_PERSONA'] == ''


def test_repoint_prompt(mgr):
    assert mgr.repoint_prompt('ronnie', 'ronnie2') == ['zed']
    assert mgr._personas['zed']['settings']['prompt'] == 'ronnie2'
    assert mgr._personas['amy']['settings']['prompt'] == 'beach'


# ── prompts ──

@pytest.fixture
def presets(tmp_path, monkeypatch, mgr, world):
    import sys
    import core.prompt_state as pst
    import core.personas.persona_manager  # noqa: F401 — load the module
    # core.personas re-exports the singleton under the submodule's name
    pm_mod = sys.modules['core.personas.persona_manager']
    ps = {'ronnie': {'character': 'ronnie', 'extras': [], '_privacy_required': False},
          'beach': {'character': 'beach', 'extras': []}}
    saves = []
    monkeypatch.setattr(prompt_manager, '_scenario_presets', ps)
    monkeypatch.setattr(prompt_manager, 'save_scenario_presets',
                        lambda reason=None, audit=True: saves.append(reason) or True)
    monkeypatch.setattr(pst, '_assembled_state',
                        {'character': 'ronnie', 'extras': [], 'spice': '',
                         'active_preset': 'ronnie'})
    monkeypatch.setattr(pm_mod, 'persona_manager', mgr)
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'no_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'no_refs.json')
    monkeypatch.setattr(pv, '_refs_loaded', False)
    return SimpleNamespace(ps=ps, saves=saves, pst=pst)


def test_prompt_rename_moves_and_repoints(presets, mgr, world):
    ok, msg = crud.rename_prompt('ronnie', 'ronnie2')
    assert ok, msg
    assert 'ronnie2' in presets.ps and 'ronnie' not in presets.ps
    assert presets.saves
    assert mgr._personas['zed']['settings']['prompt'] == 'ronnie2'
    assert world.chats['c1']['prompt'] == 'ronnie2'
    assert world.chats['c2']['prompt'] == 'beach'
    assert world.tasks[0]['prompt'] == 'ronnie2'
    assert presets.pst._assembled_state['active_preset'] == 'ronnie2'


def test_prompt_rename_onto_taken_name_refuses(presets, world):
    before = {k: dict(v) for k, v in presets.ps.items()}
    ok, msg = crud.rename_prompt('ronnie', 'beach')
    assert not ok and 'already exists' in msg
    assert presets.ps == before
    assert world.chats['c1']['prompt'] == 'ronnie'


@pytest.mark.parametrize('bad', ['', 'default', 'assembled', '_x', 'a/b', 'ronnie'])
def test_prompt_rename_bad_names(presets, bad):
    ok, _ = crud.rename_prompt('ronnie', bad)
    assert not ok
    assert 'ronnie' in presets.ps


def test_prompt_rename_store_failure_rolls_back(presets, monkeypatch):
    monkeypatch.setattr(prompt_manager, 'save_scenario_presets',
                        lambda reason=None, audit=True: False)
    ok, _ = crud.rename_prompt('ronnie', 'ronnie2')
    assert not ok
    assert 'ronnie' in presets.ps and 'ronnie2' not in presets.ps


# ── scheduler ──

def test_scheduler_repoint_ref():
    from core.continuity.scheduler import ContinuityScheduler
    s = ContinuityScheduler.__new__(ContinuityScheduler)
    s._lock = threading.RLock()
    s._tasks = {'a': {'name': 'A', 'persona': 'zed'}, 'b': {'name': 'B', 'persona': 'amy'}}
    saved = []
    s._save_tasks = lambda: saved.append(1)
    assert s.repoint_ref('persona', 'zed', 'zed2') == ['A']
    assert s._tasks['a']['persona'] == 'zed2' and s._tasks['b']['persona'] == 'amy'
    assert saved == [1]
    assert s.repoint_ref('persona', 'nobody', 'x') == [] and saved == [1]


# ── avatars ──

def test_avatar_survives_rename_and_new_namesake(mgr, world):
    f1 = mgr.set_avatar('zed', b'green')
    assert f1 and f1.startswith('zed-') and f1.endswith('.webp')
    assert mgr.update('zed', {'name': 'zed2'})
    assert mgr.create('zed', {'settings': {}})
    f2 = mgr.set_avatar('zed', b'blue')
    assert f2 != f1
    assert mgr.get_avatar_path('zed2').read_bytes() == b'green'
    assert mgr.delete('zed')
    assert mgr.get_avatar_path('zed2').read_bytes() == b'green'
    assert not (mgr.USER_AVATARS / f2).exists()


def test_avatar_replace_removes_old_file_unless_shared(mgr, world):
    f1 = mgr.set_avatar('zed', b'one')
    mgr._personas['amy']['avatar'] = f1          # legacy shared reference
    f2 = mgr.set_avatar('zed', b'two')
    assert (mgr.USER_AVATARS / f1).exists()      # amy still holds it
    mgr._personas['amy']['avatar'] = None
    assert mgr.delete_avatar('zed')
    assert not (mgr.USER_AVATARS / f2).exists()


def test_avatar_save_failure_rolls_back(mgr, monkeypatch):
    monkeypatch.setattr(mgr, '_save_to_user', lambda: False)
    assert mgr.set_avatar('zed', b'x') is None
    assert mgr._personas['zed']['avatar'] is None
    assert not list(mgr.USER_AVATARS.iterdir())
