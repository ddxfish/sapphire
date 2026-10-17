"""Importance dynamics (2026-07-12) — decay + reinforcement-on-recall.

The observation-week contract: numbers MOVE, nothing newly MATTERS.
NULL never touched, core band (>=0.9) and favorites exempt, boost ceiling
0.85, decay floor 0.1, day-gated boost, `updated` never bumped.
"""
import importlib.util
import re

import pytest

from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import dynamics


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_dark_cache", None, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    monkeypatch.setattr(dynamics, "_rates", lambda: {'decay': 0.005, 'boost': 0.02})
    return pt


def _add(content, importance=None, favorite=0, scope='default'):
    msg, ok = pt._save_memory(content, scope)
    assert ok, msg
    cid = int(re.search(r'ID: (\d+)', msg).group(1))
    with pt._get_connection() as conn:
        conn.execute('UPDATE chunks SET importance = ?, favorite = ? WHERE id = ?',
                     (importance, favorite, cid))
        conn.commit()
    return cid


def _get(cid, cols='importance, recall_count, last_recalled, updated'):
    with pt._get_connection() as conn:
        return conn.execute(f'SELECT {cols} FROM chunks WHERE id = ?', (cid,)).fetchone()


# ─── Decay ───────────────────────────────────────────────────────────────────

def test_decay_moves_rated_only(palace):
    rated = _add('an ordinary tuesday', importance=0.5)
    unrated = _add('an unrated wednesday')
    fav = _add('the hare', importance=0.95, favorite=1)
    core = _add('who I am', importance=0.92)

    touched = dynamics.decay_tick('default')
    assert touched == 1
    assert _get(rated)[0] == pytest.approx(0.495)
    assert _get(unrated)[0] is None            # NULL never touched
    assert _get(fav)[0] == pytest.approx(0.95)  # favorites exempt
    assert _get(core)[0] == pytest.approx(0.92)  # core band exempt


def test_decay_floor_clamps_and_rests(palace):
    near = _add('nearly faded', importance=0.103)
    resting = _add('already resting', importance=0.1)
    below = _add('hand-rated below floor', importance=0.05)

    dynamics.decay_tick('default')
    assert _get(near)[0] == pytest.approx(0.1)      # clamped, not overshot
    assert _get(resting)[0] == pytest.approx(0.1)   # at floor: untouched
    assert _get(below)[0] == pytest.approx(0.05)    # below floor: never raised


def test_decay_never_bumps_updated(palace):
    cid = _add('quiet memory', importance=0.5)
    before = _get(cid)[3]
    dynamics.decay_tick('default')
    assert _get(cid)[3] == before


def test_decay_rate_zero_disables(palace, monkeypatch):
    monkeypatch.setattr(dynamics, "_rates", lambda: {'decay': 0.0, 'boost': 0.02})
    cid = _add('should not move', importance=0.5)
    assert dynamics.decay_tick('default') == 0
    assert _get(cid)[0] == pytest.approx(0.5)


# ─── Boost ───────────────────────────────────────────────────────────────────

def test_boost_day_gate_and_instrumentation(palace):
    cid = _add('the garden project', importance=0.5)
    dynamics.boost_recall('default', [cid])
    imp, rc, lr, _u = _get(cid)
    assert imp == pytest.approx(0.52)
    assert rc == 1 and lr is not None

    dynamics.boost_recall('default', [cid])   # same day: no second bump
    imp, rc, _lr, _u = _get(cid)
    assert imp == pytest.approx(0.52)
    assert rc == 2                              # …but every recall is counted


def test_boost_null_counts_but_never_rates(palace):
    cid = _add('unrated but findable')
    dynamics.boost_recall('default', [cid])
    imp, rc, lr, _u = _get(cid)
    assert imp is None and rc == 1 and lr is not None


def test_boost_ceiling_and_core_exempt(palace):
    near = _add('almost at ceiling', importance=0.84)
    above = _add('between ceiling and core', importance=0.86)
    core = _add('core memory', importance=0.95)

    dynamics.boost_recall('default', [near, above, core])
    assert _get(near)[0] == pytest.approx(0.85)    # clamped at ceiling
    assert _get(above)[0] == pytest.approx(0.86)   # >= ceiling: untouched
    assert _get(core)[0] == pytest.approx(0.95)    # core band untouched


def test_search_seam_boosts_direct_hits(palace):
    cid = _add('the lighthouse at dusk was beautiful', importance=0.5)
    text, ok = pt._search_memory('lighthouse dusk', 'default')
    assert ok and 'lighthouse' in text
    imp, rc, _lr, _u = _get(cid)
    assert imp == pytest.approx(0.52) and rc == 1


# ─── Nightly handler ─────────────────────────────────────────────────────────

class _FakeState(dict):
    def save(self, k, v):
        self[k] = v


def _load_nightly():
    path = pt.Path(__file__).parent.parent / 'schedule' / 'nightly.py'
    spec = importlib.util.spec_from_file_location('mp_nightly_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nightly_decays_once_then_passes_each_scope(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    from plugins.mindpalace.tools import librarian

    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {
        'librarian_nightly_enabled': True,
        'librarian_enabled': True,
        'importance_enabled': True,
        'librarian_nightly_scopes': ['default', 'work'],
    })
    passes = []
    monkeypatch.setattr(librarian, 'run_blocking',
                        lambda scope, what='all', kind='sort', chat=None:
                        (passes.append((kind, scope)) or (f'tidy {scope}', True)))
    cid = _add('drifts once', importance=0.5)
    for sc in ('default', 'work'):    # residency opt-in (Self page pills)
        pt.set_scope_resident(sc, passes={k: True for k in librarian.PASS_KINDS})
    nightly = _load_nightly()
    state = _FakeState()

    out = nightly.run({'plugin_state': state})
    # Pipeline order, scope by scope within each kind (toggles default ON).
    assert passes == [('dates', 'default'), ('dates', 'work'),
                      ('link', 'default'), ('link', 'work'),
                      ('dedup', 'default'), ('dedup', 'work'),
                      ('sort', 'default'), ('sort', 'work'),
                      ('self', 'default'), ('self', 'work')]
    assert _get(cid)[0] == pytest.approx(0.495)
    assert 'decay' in out and 'tidy default' in out

    out = nightly.run({'plugin_state': state})   # refire same day
    assert _get(cid)[0] == pytest.approx(0.495)  # day guard: no double decay
    assert 'already ticked' in out


def test_nightly_respects_disable_and_empty_scopes(palace, monkeypatch):
    from core.plugin_loader import plugin_loader
    nightly = _load_nightly()

    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'librarian_nightly_enabled': False})
    assert 'disabled' in nightly.run({'plugin_state': _FakeState()})

    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {
        'librarian_nightly_enabled': True, 'librarian_enabled': True,
        'librarian_nightly_scopes': []})
    assert 'no scopes' in nightly.run({'plugin_state': _FakeState()})


# ─── Core seam: time_setting → cron ──────────────────────────────────────────

def test_time_to_cron():
    from core.plugin_loader import _time_to_cron
    assert _time_to_cron('03:30', 'x') == '30 3 * * *'
    assert _time_to_cron('23:05', 'x') == '5 23 * * *'
    assert _time_to_cron('24:00', 'fallback') == 'fallback'
    assert _time_to_cron('garbage', 'fallback') == 'fallback'
    assert _time_to_cron(None, 'fallback') == 'fallback'


# ─── Alpha master toggle (2026-07-15) ────────────────────────────────────────

def test_rates_master_toggle_gates_both_halves(monkeypatch):
    """importance_enabled off (the shipping default) → _rates() is 0/0 no
    matter what the dials say. On → the dials apply."""
    from core.plugin_loader import plugin_loader
    dials = {'importance_decay_per_night': 0.01,
             'importance_boost_per_recall': 0.05}
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: dict(dials))
    assert dynamics._rates() == {'decay': 0.0, 'boost': 0.0}
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {'importance_enabled': True, **dials})
    assert dynamics._rates() == {'decay': 0.01, 'boost': 0.05}


def test_boost_disabled_still_instruments(palace, monkeypatch):
    """Master off: importance never moves, but recall_count/last_recalled
    keep recording — passive observability is not an importance write."""
    monkeypatch.setattr(dynamics, "_rates", lambda: {'decay': 0.0, 'boost': 0.0})
    cid = _add('watched but never rated', importance=0.5)
    dynamics.boost_recall('default', [cid])
    imp, rc, lr, _u = _get(cid)
    assert imp == pytest.approx(0.5) and rc == 1 and lr is not None


def test_nightly_halves_gate_independently(palace, monkeypatch):
    """Decay rides importance_enabled; librarian passes ride librarian_enabled.
    Either alone runs its half and notes the other's absence."""
    from core.plugin_loader import plugin_loader
    from plugins.mindpalace.tools import librarian
    nightly = _load_nightly()
    passes = []
    monkeypatch.setattr(librarian, 'run_blocking',
                        lambda scope, what='all', kind='sort', chat=None:
                        (passes.append((kind, scope)) or (f'tidy {scope}', True)))
    base = {'librarian_nightly_enabled': True,
            'librarian_nightly_scopes': ['default']}

    # Importance only → decay ticks, passes skipped.
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {**base, 'importance_enabled': True})
    cid = _add('drifts alone', importance=0.5)
    out = nightly.run({'plugin_state': _FakeState()})
    assert passes == [] and 'librarian disabled' in out
    assert _get(cid)[0] == pytest.approx(0.495)

    # Librarian only → the full pipeline in order, decay skipped.
    from plugins.mindpalace.tools import librarian as _lib_eng
    pt.set_scope_resident('default',
                          passes={k: True for k in _lib_eng.PASS_KINDS})
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings',
                        lambda n: {**base, 'librarian_enabled': True})
    out = nightly.run({'plugin_state': _FakeState()})
    assert passes == [('dates', 'default'), ('link', 'default'),
                      ('dedup', 'default'), ('sort', 'default'),
                      ('self', 'default')]
    assert 'importance disabled' in out
    assert _get(cid)[0] == pytest.approx(0.495)   # no second drift

    # Both off → one honest line, nothing runs.
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: dict(base))
    out = nightly.run({'plugin_state': _FakeState()})
    assert len(passes) == 5 and 'both disabled' in out


def test_nightly_pass_toggles_shape_the_recipe(palace, monkeypatch):
    """librarian_pass_<kind> off (Admin card): the nightly skips that pass
    and says so; the rest of the pipeline runs in order."""
    from core.plugin_loader import plugin_loader
    from plugins.mindpalace.tools import librarian
    nightly = _load_nightly()
    passes = []
    monkeypatch.setattr(librarian, 'run_blocking',
                        lambda scope, what='all', kind='sort', chat=None:
                        (passes.append((kind, scope)) or (f'tidy {scope}', True)))
    monkeypatch.setattr(plugin_loader, 'get_plugin_settings', lambda n: {
        'librarian_nightly_enabled': True, 'librarian_enabled': True,
        'librarian_nightly_scopes': ['default'],
        'librarian_pass_link': False, 'librarian_pass_dedup': False})
    pt.set_scope_resident('default',
                          passes={k: True for k in librarian.PASS_KINDS})
    out = nightly.run({'plugin_state': _FakeState()})
    assert passes == [('dates', 'default'), ('sort', 'default'), ('self', 'default')]
    assert 'link: off' in out and 'dedup: off' in out
