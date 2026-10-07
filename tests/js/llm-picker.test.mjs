// Corpus for shared/llm-picker.js — run under node by tests/test_llm_picker_js.py.
// THE brain picker: the provider list (Auto, None, core 🏠/☁️, a divider,
// custom providers with their baked model, a deleted saved key kept as
// "(missing)"), the model rule (hidden on auto/none, a list for core
// providers with options, free text otherwise, a saved model the list lacks
// kept), and the read. Seven hand-rolled copies collapsed here. 2026-10-07.
import { providerOptions, refreshModelControls, readModel }
    from '../../interfaces/web/static/shared/llm-picker.js';

const data = {
    providers: [
        { key: 'claude', display_name: 'Claude', enabled: true, is_core: true, is_local: false, model: 'claude-opus-5' },
        { key: 'lmstudio', display_name: 'LM Studio', enabled: true, is_core: true, is_local: true, model: '' },
        { key: 'off', display_name: 'Off', enabled: false, is_core: true },
        { key: 'lanbox', display_name: 'LAN Box', enabled: true, is_core: false, is_local: true, model: 'org/qwen-big' },
    ],
    metadata: {
        claude: { model_options: { 'claude-opus-5': 'Opus 5', 'claude-haiku-4-5': 'Haiku 4.5' } },
        lmstudio: { model_options: {} },
    },
};

let passed = 0, failed = 0;
const t = (name, fn) => { try { fn(); passed++; } catch (e) { failed++; console.log(`FAIL ${name}: ${e.message}`); } };
const assert = (c, m) => { if (!c) throw new Error(m || 'assert'); };
const el = () => ({ style: { display: '' }, innerHTML: '', value: '', disabled: false });

t('provider list: canon order, marks, divider, baked suffix, disabled hidden', () => {
    const h = providerOptions(data, 'auto');
    assert(h.startsWith('<option value="auto" selected>Auto</option><option value="none">None</option>'), h);
    assert(h.indexOf('Claude ☁') < h.indexOf('LM Studio \u{1F3E0}'), 'core order');
    assert(h.includes('<option disabled>'), 'divider');
    assert(h.indexOf('<option disabled>') < h.indexOf('"lanbox"'), 'custom after the divider');
    assert(h.includes('LAN Box (qwen-big) \u{1F3E0}'), 'custom baked suffix');
    assert(!h.includes('"off"'), 'disabled provider hidden');
});

t('provider list: options, a missing saved key, escaping', () => {
    const h = providerOptions(data, 'ghost', { includeNone: false, autoLabel: 'Auto (default)' });
    assert(h.startsWith('<option value="auto">Auto (default)</option>'), h);
    assert(!h.includes('"none"'), 'no None when asked');
    assert(h.endsWith('<option value="ghost" selected>ghost (missing)</option>'), h);
    assert(providerOptions(data, 'lanbox').includes('value="lanbox" selected'), 'saved custom selected');
    const odd = providerOptions({ providers: [{ key: 'x<y', display_name: 'a&b', enabled: true, is_core: true }] }, 'auto');
    assert(odd.includes('value="x&lt;y"') && odd.includes('>a&amp;b<'), 'escaped');
});

t('model rule: hidden on auto / none, reads blank', () => {
    const els = { select: el(), group: el(), custom: el(), customGroup: el() };
    assert(refreshModelControls(data, els, 'auto', 'x') === 'hidden');
    assert(els.group.style.display === 'none' && els.customGroup.style.display === 'none');
    assert(refreshModelControls(data, els, 'none', 'x') === 'hidden');
    assert(refreshModelControls(data, els, '', 'x') === 'hidden');
    els.select.value = 'x'; els.custom.value = 'x';
    assert(readModel(els, 'auto') === '' && readModel(els, 'none') === '');
});

t('model rule: a core provider with options gets the list, default first, orphan kept', () => {
    const els = { select: el(), group: el(), custom: el(), customGroup: el() };
    assert(refreshModelControls(data, els, 'claude', 'claude-haiku-4-5') === 'select');
    assert(els.select.innerHTML.startsWith('<option value="">Default (Opus 5)</option>'), els.select.innerHTML);
    assert(els.select.innerHTML.includes('value="claude-haiku-4-5" selected'), 'current selected');
    assert(els.group.style.display === '' && els.customGroup.style.display === 'none');
    assert(els.select.disabled === false);
    refreshModelControls(data, els, 'claude', 'retired-model');
    assert(els.select.innerHTML.endsWith('<option value="retired-model" selected>retired-model</option>'), 'orphan kept');
    els.select.value = 'claude-haiku-4-5';
    assert(readModel(els, 'claude') === 'claude-haiku-4-5');
});

t('model rule: custom and option-less providers get free text, blank = provider default', () => {
    const els = { select: el(), group: el(), custom: el(), customGroup: el() };
    assert(refreshModelControls(data, els, 'lanbox', 'qwen-small') === 'text');
    assert(els.custom.value === 'qwen-small' && els.customGroup.style.display === '' && els.group.style.display === 'none');
    assert(refreshModelControls(data, els, 'lmstudio', '') === 'text', 'core with empty options → text');
    els.custom.value = '  typed-model ';
    assert(readModel(els, 'lanbox') === 'typed-model');
    els.custom.value = '';
    assert(readModel(els, 'lanbox') === '');
});

t('a control can be its own group (the Resident strip)', () => {
    const sel = el(), inp = el();
    const els = { select: sel, group: sel, custom: inp, customGroup: inp };
    refreshModelControls(data, els, 'claude', '');
    assert(sel.style.display === '' && inp.style.display === 'none');
    refreshModelControls(data, els, 'lanbox', '');
    assert(sel.style.display === 'none' && inp.style.display === '');
    refreshModelControls(data, els, 'auto', '');
    assert(sel.style.display === 'none' && inp.style.display === 'none');
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
