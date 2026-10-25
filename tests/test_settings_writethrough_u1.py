"""U1 layers (a)+(b) — the Settings view stops being a write-only cache (2026-09-20,
record tmp/model-roster-20260920.md "U1 DESIGN DEEP DIVE"). Source contracts: the
three tab-switch doors share one refetching goToTab, the refetch is sequence-guarded,
every self-persisting write site commits through ctx (never `ctx.settings.X = …`),
and commits follow a confirmed write. Layer (c) — the bus join — is NOT here yet."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'interfaces/web/static'
# 2026-10-22 split (tmp/setup-wizard-plan.md): the SHELL (tab rail, goToTab, the
# three doors) stays in views/settings.js; the HOST (snapshot, sequence guards,
# ctx, batch save) moved to shared/settings-host.js so the setup wizard can mount
# the same tab modules.
SHELL = (STATIC / 'views/settings.js').read_text(encoding='utf-8')
HOST = (STATIC / 'shared/settings-host.js').read_text(encoding='utf-8')
TABS = STATIC / 'views/settings-tabs'


def _tab(name):
    return (TABS / f'{name}.js').read_text(encoding='utf-8')


# ── layer (a): one refetching door ──────────────────────────────────────────

def test_three_doors_share_one_refetching_goToTab():
    assert 'async function goToTab(tabId)' in SHELL
    assert SHELL.count('goToTab(') >= 4          # definition + settings-navigate + sidebar + mobile
    body = SHELL[SHELL.index('async function goToTab(tabId)'):SHELL.index('let _shellNavBound')]
    assert 'flushCurrentInputs();' in body and 'await host.loadSettingsOnly();' in body
    assert 'if (activeTab !== tabId) return;' in body     # a later click owns the paint
    assert 'renderTabContent();' in body
    # the old copies are gone: each door hands off to goToTab and paints nothing itself
    doors = [
        SHELL[SHELL.index("container.addEventListener('settings-navigate'"):SHELL.index('_shellNavBound = true;')],
        SHELL[SHELL.index(".settings-sidebar')?.addEventListener('click'"):SHELL.index('// Mobile tab dropdown')],
        SHELL[SHELL.index("mobileMenu.addEventListener('click'"):SHELL.index('const outsideHandler')],
    ]
    for door in doors:
        assert 'goToTab(' in door and 'renderTabContent(' not in door and 'flushCurrentInputs(' not in door


def test_refetches_are_sequence_guarded():
    assert 'let _loadSeq = 0;' in HOST
    assert HOST.count('const seq = ++_loadSeq;') == 2 and HOST.count('if (seq !== _loadSeq) return;') == 2


def test_tab_switch_refetch_is_settings_only():
    fn = HOST[HOST.index('async function loadSettingsOnly()'):HOST.index('async function loadPluginList()')]
    assert 'api.getAllSettings()' in fn and 'loadProviderMeta()' in fn
    assert 'loadPluginList' not in fn and 'loadThemes' not in fn


def test_global_save_counts_in_flight():
    assert 'let _saveInFlight = 0;' in HOST
    save = HOST[HOST.index('async function saveChanges()'):HOST.index('// ── Help Popup ──')]
    assert '_saveInFlight++;' in save and '_saveInFlight--;' in save


# ── layer (b): commit write-through ─────────────────────────────────────────

def test_ctx_exposes_commit_and_commitMerge_over_module_state():
    assert "import { commitInto, mergeInto } from './settings-commit.js';" in HOST
    assert 'commit(key, value) { commitInto({ settings, pendingChanges }, key, value); }' in HOST
    assert 'commitMerge(mapKey, subKey, updates) { mergeInto({ settings, pendingChanges }, mapKey, subKey, updates); }' in HOST


def test_no_hand_patches_of_the_snapshot_remain():
    pat = re.compile(r'ctx\.settings\.[A-Za-z_]+\s*=[^=]')
    for f in sorted(TABS.glob('*.js')):
        assert not pat.search(f.read_text(encoding='utf-8')), f.name


def test_every_inventoried_site_commits():
    llm = _tab('llm')
    assert llm.count('ctx.commitMerge(') == 6     # core enable, field PUT, model select, custom model, custom enable, custom card autosave
    assert llm.count("ctx.commit('LLM_FALLBACK_ORDER'") == 2 and "ctx.commit('MODEL_GENERATION_PROFILES'" in llm
    assert "function _mapFor(ctx, key)" in llm
    tools = _tab('tools')
    assert tools.count('ctx.commit(') == 5
    assert "ctx.commit('DEFAULT_BACKGROUND', name)" in _tab('appearance')
    assert "ctx.commit('ALLOW_UNSIGNED_PLUGINS', enabling)" in _tab('plugins')
    assert "ctx.commit('BACKUPS_EXCLUDE_PATTERNS', lines)" in _tab('backup')
    dash = _tab('dashboard')
    # optional-chained: these handlers are reachable without a ctx (dashboard mounts outside Settings too)
    assert "ctx?.commit?.('DASHBOARD_DISPLAY_NAME'" in dash and "ctx?.commit?.('METRICS_ENABLED'" in dash
    assert dash.index('if (r.ok)') < dash.index("ctx?.commit?.('DASHBOARD_DISPLAY_NAME'")


def test_commits_follow_confirmed_writes():
    # backup: the batch PUT is checked before the commit; appearance: commit rides .then()
    b = _tab('backup')
    assert 'if (!r.ok) throw' in b and b.index('if (!r.ok)') < b.index("ctx.commit('BACKUPS_EXCLUDE_PATTERNS'")
    a = _tab('appearance')
    assert ".then(() => ctx.commit('DEFAULT_BACKGROUND', name))" in a
    # tools: save() rethrows, so a commit after `await save(` is confirmed-only
    t = _tab('tools')
    assert 'throw e;' in t[t.index('async function save(patch)'):t.index('function rerender')]
