// shared/llm-picker.js — THE LLM brain picker (2026-10-07).
//
// One home for "choose a provider + model pair": the chat sidebar, the
// persona editor, the room/story brain section, the trigger editor and the
// Mind Palace Resident strip all render through these three helpers. Before
// this, seven hand-written copies of "fill the model dropdown from the
// provider" had drifted on their own days: one saved a custom provider's
// baked model as a pin, one hid the model on auto while another showed it
// disabled, one read a field the API doesn't send, one filled on a 50 ms
// timer. The canonical behavior is the chat sidebar's:
//   - provider list: Auto, (None), core providers 🏠/☁️, a divider, then
//     custom providers labelled with their baked model
//   - a saved key the list no longer has stays selectable as "(missing)":
//     Save reads .value blind, so snapping to the first option would
//     silently overwrite the real binding
//   - auto / none: the model controls HIDE — the resolver drops a model
//     beside auto and the stores refuse it (core/chat/llm_providers/resolve.py,
//     the pair rule)
//   - a core provider with model_options: a select, "Default (name)" first,
//     a saved model the list lacks kept as its own option
//   - anything else (custom / generic): a free-text model box, '' = the
//     provider's own default
//
// DOM contract: the surface owns its markup and hands the elements in; the
// picker fills, shows/hides and reads. Saving stays with the surface. Data
// is {providers, metadata} from /api/llm/providers (fetchLLMProviders in
// shared/continuity-api.js) — fetched by the surface, never cached here.

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const glyph = p => p.is_local === undefined ? '' : (p.is_local ? ' \u{1F3E0}' : ' ☁️');
const baked = p => p.model ? ` (${p.model.split('/').pop()})` : '';

/**
 * The provider <option> list for a <select>. `current` is the saved key
 * ('auto' when blank). includeNone offers "None" (LLM off): chats, personas
 * and rooms yes; tasks and the librarian no — a brain that never answers is
 * not a choice there.
 */
export function providerOptions(data, current = 'auto', { includeNone = true, autoLabel = 'Auto' } = {}) {
    const providers = (data?.providers || []).filter(p => p.enabled);
    const cur = current || 'auto';
    const sel = k => k === cur ? ' selected' : '';
    let html = `<option value="auto"${sel('auto')}>${esc(autoLabel)}</option>`;
    if (includeNone) html += `<option value="none"${sel('none')}>None</option>`;
    const core = providers.filter(p => p.is_core);
    const custom = providers.filter(p => !p.is_core);
    html += core.map(p =>
        `<option value="${esc(p.key)}"${sel(p.key)}>${esc(p.display_name || p.key)}${glyph(p)}</option>`).join('');
    if (custom.length) {
        if (core.length) html += '<option disabled>────────</option>';
        html += custom.map(p =>
            `<option value="${esc(p.key)}"${sel(p.key)}>${esc(p.display_name || p.key)}${esc(baked(p))}${glyph(p)}</option>`).join('');
    }
    if (cur !== 'auto' && cur !== 'none' && !providers.some(p => p.key === cur)) {
        html += `<option value="${esc(cur)}" selected>${esc(cur)} (missing)</option>`;
    }
    return html;
}

/**
 * Fill and show the model controls for providerKey.
 * els: { select, group, custom, customGroup } — any may be absent; a group
 * may be the control itself. Returns 'hidden' | 'select' | 'text'.
 */
export function refreshModelControls(data, els, providerKey, currentModel = '') {
    const { select, group, custom, customGroup } = els || {};
    if (group) group.style.display = 'none';
    if (customGroup) customGroup.style.display = 'none';
    const key = providerKey || 'auto';
    if (key === 'auto' || key === 'none') return 'hidden';
    const opts = (data?.metadata || {})[key]?.model_options;
    const conf = (data?.providers || []).find(p => p.key === key);
    if (select && opts && Object.keys(opts).length) {
        const def = conf?.model || '';
        const defLabel = def ? `Default (${opts[def] || def})` : 'Default';
        let html = `<option value="">${esc(defLabel)}</option>` + Object.entries(opts).map(([k, v]) =>
            `<option value="${esc(k)}"${k === currentModel ? ' selected' : ''}>${esc(v)}</option>`).join('');
        if (currentModel && !opts[currentModel]) {
            html += `<option value="${esc(currentModel)}" selected>${esc(currentModel)}</option>`;
        }
        select.innerHTML = html;
        select.disabled = false;
        if (group) group.style.display = '';
        return 'select';
    }
    if (custom) {
        custom.value = currentModel || '';
        if (customGroup) customGroup.style.display = '';
        return 'text';
    }
    return 'hidden';
}

/** The model the controls currently show: '' for auto/none or "default". */
export function readModel(els, providerKey) {
    const key = providerKey || 'auto';
    if (key === 'auto' || key === 'none') return '';
    const { select, group, custom, customGroup } = els || {};
    if (group && group.style.display !== 'none') return select?.value || '';
    if (customGroup && customGroup.style.display !== 'none') return (custom?.value || '').trim();
    return '';
}
