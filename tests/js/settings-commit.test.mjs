// Corpus for shared/settings-commit.js — run under node by tests/test_settings_commit_js.py.
// U1 (b), 2026-09-20: the Settings view's write-through primitives. Proves
// (1) a committed key is no longer pending, (2) a sub-key merge keeps sibling
// entries and sibling fields, (3) the merge replaces the map object so a
// reference captured before the write can't observe it, (4) the primitives
// read state at CALL time (a reassigned snapshot is honored).
import { commitInto, mergeInto } from '../../interfaces/web/static/shared/settings-commit.js';

let passed = 0, failed = 0;
function check(name, ok) { if (ok) passed++; else { failed++; console.error('FAIL', name); } }

// 1. commit clears pending for that key only
{
    const st = { settings: { A: 1, B: 2 }, pendingChanges: { A: 9, B: 8 } };
    commitInto(st, 'A', 5);
    check('commit writes value', st.settings.A === 5);
    check('commit clears its pending', !('A' in st.pendingChanges));
    check('commit leaves other pending', st.pendingChanges.B === 8);
}

// 2. merge keeps siblings (entries and fields)
{
    const st = { settings: { LLM_PROVIDERS: { claude: { model: 'old', enabled: false }, openai: { model: 'x' } } },
                 pendingChanges: { LLM_PROVIDERS: 'phantom' } };
    mergeInto(st, 'LLM_PROVIDERS', 'claude', { model: 'claude-opus-5' });
    check('merge updates the field', st.settings.LLM_PROVIDERS.claude.model === 'claude-opus-5');
    check('merge keeps sibling field', st.settings.LLM_PROVIDERS.claude.enabled === false);
    check('merge keeps sibling entry', st.settings.LLM_PROVIDERS.openai.model === 'x');
    check('merge clears the map pending', !('LLM_PROVIDERS' in st.pendingChanges));
}

// 3. merge replaces the map object; a stale reference stays stale
{
    const before = { a: { v: 1 } };
    const st = { settings: { M: before }, pendingChanges: {} };
    mergeInto(st, 'M', 'a', { v: 2 });
    check('merge makes a new map', st.settings.M !== before);
    check('old reference untouched', before.a.v === 1);
    check('new entry when subkey absent', (mergeInto(st, 'M', 'b', { v: 3 }), st.settings.M.b.v === 3));
    check('missing map is created', (mergeInto(st, 'NEW', 'k', { z: 1 }), st.settings.NEW.k.z === 1));
}

// 4. state is read at call time — the view reassigns both objects on refetch/save
{
    let settings = { A: 1 }, pendingChanges = { A: 2 };
    const ctx = { commit: (k, v) => commitInto({ settings, pendingChanges }, k, v) };
    settings = { A: 10 }; pendingChanges = { A: 20 };       // "loadData() + Save reassigned them"
    ctx.commit('A', 7);
    check('commit lands in the CURRENT snapshot', settings.A === 7);
    check('commit clears the CURRENT pending', !('A' in pendingChanges));
}

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
