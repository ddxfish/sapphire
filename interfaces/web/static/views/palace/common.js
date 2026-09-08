// views/palace/common.js - Shared helpers for the Mind Palace view trio
// (memories / entities / knowledge). Mirrors shared/mind-common.js idioms so
// the palace feels like the same Mind the user already knows.
import { csrfHeaders, escHtml, timeAgo } from '../../shared/mind-common.js';
import { showExportDialog, showImportDialog } from '../../shared/import-export.js';
import { sessionId } from '../../shared/fetch.js';

export const API = '/api/plugin/mindpalace';
export const SCOPE_ENDPOINT = `${API}/scopes`;

// Same tab ids as MIND_TABS (ids === view ids, routing unchanged) — only the
// People label shifts: the palace L2 is people/places/THINGS. Self (L0) leads
// the strip — it's the palace-only layer and the identity root of the graph.
// ONE Knowledge tab (2026-07-11): human and AI knowledge are the same layer;
// who wrote it is metadata (meta.added_by), filterable inside the tab.
export const PALACE_TABS = [
    { id: 'self', label: 'Self', icon: '\u{1F4A0}' },
    { id: 'memories', label: 'Memories', icon: '\u{1F9E0}' },
    { id: 'people', label: 'Entities', icon: '\u{1F465}' },
    { id: 'knowledge', label: 'Knowledge', icon: '\u{1F4DA}' },
    { id: 'goals', label: 'Goals', icon: '\u{1F3AF}' },
    { id: 'admin', label: 'Admin', icon: '\u{1F6E0}️' },
];

// Plugin layers (v1.1): registered layers get their own tabs after the core
// five. Splices PALACE_TABS in place so every view's next render sees them;
// call at the top of show(). Short cache — one fetch per Mind visit.
export let PLUGIN_LAYERS = [];
let _layersTs = 0;
export async function refreshPalaceTabs() {
    if (Date.now() - _layersTs < 10_000) return PALACE_TABS;
    _layersTs = Date.now();
    try {
        PLUGIN_LAYERS = (await palaceGet('layers')).layers || [];
    } catch {
        PLUGIN_LAYERS = [];
    }
    for (let i = PALACE_TABS.length - 1; i >= 0; i--) {
        if (PALACE_TABS[i].id.startsWith('layer-')) PALACE_TABS.splice(i, 1);
    }
    // Layer tabs slot in BEFORE Admin — the operator console stays last.
    for (const l of PLUGIN_LAYERS) {
        PALACE_TABS.splice(PALACE_TABS.findIndex(t => t.id === 'admin'), 0,
            { id: `layer-${l.key}`, label: l.label, icon: l.icon || '\u{1F9E9}' });
    }
    return PALACE_TABS;
}

// ─── Remembered scope — tab switches respect the last sidebar pick ──────────
// (Krem 2026-07-19: every tab hop reset scope to the chat's default.)
// Priority at tab-show: explicit handoff (window._mindScope) > remembered >
// the active chat's scope. Survives reloads via localStorage.
let _rememberedScope = null;
export function rememberMindScope(s) {
    _rememberedScope = s || null;
    try { localStorage.setItem('palace_scope', s || ''); } catch {}
}
export function recallMindScope() {
    if (_rememberedScope) return _rememberedScope;
    try { return localStorage.getItem('palace_scope') || null; } catch { return null; }
}

export async function palaceGet(path) {
    const r = await fetch(`${API}/${path}`, { credentials: 'same-origin' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    return data;
}

export async function palaceSend(path, method, body) {
    const r = await fetch(`${API}/${path}`, {
        method,
        credentials: 'same-origin',
        // X-Session-ID → the server stamps `origin` on the mind_changed it
        // publishes, so THIS tab skips its own echo (every palace click used
        // to paint twice — DOM-refresh hunt 2026-09-08). Views re-render
        // locally after their own writes; the echo is for other tabs.
        headers: csrfHeaders({ 'Content-Type': 'application/json', 'X-Session-ID': sessionId }),
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
    return data;
}

// Concrete-stakes line for the scope-delete confirm (Krem's fork-B ruling,
// 2026-07-11): the typed-DELETE box shows exactly what the whole scope holds.
export async function describeScopeForDelete(name) {
    try {
        const st = await palaceGet(`status?scope=${encodeURIComponent(name)}`);
        const L = st.layers || {};
        const core = ['events', 'self', 'entities', 'knowledge', 'goals'];
        const extra = Object.entries(L)
            .filter(([k]) => !core.includes(k))
            .reduce((a, [, n]) => a + n, 0);
        return `Right now “${name}” holds ${L.events || 0} memories, `
            + `${L.self || 0} self entries, ${st.entities || 0} entities, `
            + (extra ? `${extra} plugin-layer chunks, ` : '')
            + `and ${L.knowledge || 0} knowledge chunks — ALL of it goes.`;
    } catch { return ''; }
}

// Per-tab, per-scope import/export (2026-07-11). One layer × one scope per
// file; import lands in the CURRENTLY VIEWED scope, additive + idempotent.
export function transferButtons() {
    return `<button class="mind-btn-sm" data-transfer="export" title="Download this tab's data for the current scope">⬇ Export</button>
            <button class="mind-btn-sm" data-transfer="import" title="Import a matching export file into the current scope">⬆ Import</button>`;
}

// Uses the app-standard io dialogs (clipboard + file), same as personas/
// prompts/toolsets — one import/export experience everywhere.
export function bindTransfer(el, layer, getScope, ui, onDone) {
    el.querySelector('[data-transfer="export"]')?.addEventListener('click', async () => {
        const scope = getScope();
        try {
            const data = await palaceGet(`transfer/export?scope=${encodeURIComponent(scope)}&layer=${encodeURIComponent(layer)}`);
            showExportDialog({
                type: 'Mind Palace',
                name: `${layer} (${scope})`,
                data,
                filename: `mindpalace-${layer}-${scope}-${new Date().toISOString().slice(0, 10)}.json`,
            });
        } catch (e) { ui.showToast(`Export failed: ${e.message}`, 'error'); }
    });
    el.querySelector('[data-transfer="import"]')?.addEventListener('click', () => {
        showImportDialog({
            type: `Mind Palace ${layer}`,
            existingNames: [],
            validate: (parsed) => {
                if (parsed?.format !== 'mindpalace-export') return 'Not a Mind Palace export file';
                if (parsed.layer && parsed.layer !== layer)
                    return `This is a "${parsed.layer}" export — import it from that tab`;
                return null;
            },
            getName: (parsed) => `${(parsed.chunks || []).length} chunks into "${getScope()}"`,
            onImport: async (parsed) => {
                const r = await palaceSend('transfer/import', 'POST',
                    { scope: getScope(), expect_layer: layer, data: parsed });
                const bits = [`${r.imported} imported`, `${r.skipped} skipped`];
                if (r.entities_upserted) bits.push(`${r.entities_upserted} entities`);
                if (r.edges_seeded) bits.push(`${r.edges_seeded} connections`);
                if (r.arrived_as_history) bits.push(`${r.arrived_as_history} as history`);
                ui.showToast(`Import: ${bits.join(', ')}`, 'success');
                onDone && onDone();
                return false;   // counts toast above is the feedback — skip the dialog's generic one
            },
        });
    });
}

export function labelHue(label) {
    if (!label) return 220;
    let h = 0;
    for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
    return h;
}

export function labelChip(label) {
    if (!label) return '';
    const hue = labelHue(label);
    return `<span class="mind-mem-label" style="background:hsl(${hue},60%,18%);color:hsl(${hue},80%,72%);border:1px solid hsl(${hue},60%,32%)">${escHtml(label)}</span>`;
}

export function layerChip(layer) {
    return `<span class="palace-layer palace-layer-${escHtml(layer)}">${escHtml(layer)}</span>`;
}

export function keyPill(privateKey) {
    return privateKey
        ? `<span class="mind-mem-key" title="Gated by this private key — only AI calls passing this key can see it">\u{1F512} ${escHtml(privateKey)}</span>`
        : '';
}

export function favStar(id, favorite) {
    return `<button class="palace-fav ${favorite ? 'is-fav' : ''}" data-id="${id}" title="${favorite ? 'Favorite — never fades. Click to unfavorite.' : 'Mark favorite (never fades)'}">${favorite ? '★' : '☆'}</button>`;
}

// The metadata window — every chunk carries its meta JSON; this renders it
// as a compact expandable panel instead of raw JSON.
export function metaPanel(meta) {
    if (!meta) return '';
    const rows = [];
    const prov = ['persona', 'model', 'chat', 'channel', 'session_id']
        .filter(k => meta[k]).map(k => `<span class="palace-meta-kv"><b>${k.replace('_id', '')}</b> ${escHtml(String(meta[k]))}</span>`);
    if (prov.length) rows.push(`<div class="palace-meta-row">${prov.join('')}</div>`);
    if (meta.refers_to_time?.length)
        rows.push(`<div class="palace-meta-row"><b>time refs</b> ${meta.refers_to_time.map(t => `<span class="palace-pill">${escHtml(t)}</span>`).join('')}</div>`);
    if (meta.noun_candidates?.length)
        rows.push(`<div class="palace-meta-row"><b>nouns</b> ${meta.noun_candidates.map(n => `<span class="palace-pill palace-pill-dim">${escHtml(n)}</span>`).join('')}</div>`);
    if (meta.stats) {
        const s = meta.stats;
        const bits = [`${s.words ?? '?'} words`];
        if (s.question) bits.push('question');
        if (s.url) bits.push('url');
        if (s.code) bits.push('code');
        rows.push(`<div class="palace-meta-row palace-meta-dim">${bits.map(escHtml).join(' · ')}</div>`);
    }
    if (meta.import_key) rows.push(`<div class="palace-meta-row palace-meta-dim">imported (${escHtml(String(meta.import_key))})</div>`);
    if (!rows.length) return '';
    return `<details class="palace-meta"><summary>meta</summary>${rows.join('')}</details>`;
}

export function chunkCard(c, { showLayer = true } = {}) {
    const tierChip = c.tier ? `<span class="palace-tier palace-tier-${c.tier}" title="Tier ${c.tier}: ${['', 'headline', 'facts', 'trivia'][c.tier] || ''}">T${c.tier}</span>` : '';
    const entChip = c.entity_name ? `<span class="palace-ent-chip">${escHtml(c.entity_name)}</span>` : '';
    const author = c.meta?.added_by;
    const isPlugin = typeof author === 'string' && author.startsWith('plugin:');
    const authorPill = author
        ? `<span class="palace-author-pill" title="Added by ${isPlugin ? escHtml(author.slice(7)) + ' (plugin)' : (author === 'ai' ? 'Sapphire' : 'you')}">${isPlugin ? '\u{1F9E9}' : (author === 'ai' ? '\u{1F916}' : '\u{1F464}')}</span>`
        : '';
    const pruned = !!c.meta?.pruned_at;
    const prunedPill = pruned
        ? `<span class="palace-pruned-pill" title="Retired by the librarian${c.meta.pruned_reason ? ': ' + escHtml(c.meta.pruned_reason) : ''} — hidden from her recall, restorable">\u{1F9F9} retired</span>`
        : '';
    // Archived self-sheet versions (identity/values/projects keep their
    // becoming-history) — visibly older editions, not current sheet state.
    const superseded = !!c.meta?.superseded_at;
    const historyPill = superseded
        ? `<span class="palace-superseded-pill" title="Archived version — replaced ${escHtml(String(c.meta.superseded_at).slice(0, 10))}. The Self tab shows the current one; 📜 on its card lists these.">\u{1F4DC} history</span>`
        : '';
    return `
        <div class="ui-card palace-chunk${pruned ? ' palace-chunk-pruned' : ''}${superseded ? ' palace-chunk-superseded' : ''}" data-id="${c.id}">
            <button class="ui-card-x palace-del-chunk" data-id="${c.id}" title="Delete">✕</button>
            <div class="ui-card-meta" style="margin-top:0;padding-right:26px">
                ${showLayer ? layerChip(c.layer) : ''}
                ${tierChip}${entChip}${authorPill}
                ${labelChip(c.label)}${prunedPill}${historyPill}
                ${keyPill(c.private_key)}
                <span class="ui-meta-text" style="margin-left:auto">${escHtml(timeAgo(c.created))} · [${c.id}]</span>
                ${favStar(c.id, c.favorite)}
            </div>
            <div class="ui-card-body">${escHtml(c.content)}</div>
            ${metaPanel(c.meta)}
            ${pruned ? `<div class="ui-card-actions"><button class="mind-btn-sm palace-unprune" data-id="${c.id}" title="Restore to her recall">↩ restore</button></div>` : ''}
        </div>`;
}

// Shared binder for chunk-card actions inside a container. onChange re-renders.
export function bindChunkCards(el, onChange, ui) {
    el.querySelectorAll('.palace-fav').forEach(btn => {
        btn.addEventListener('click', async () => {
            const fav = !btn.classList.contains('is-fav');
            try {
                await palaceSend(`chunks/${btn.dataset.id}/favorite`, 'POST', { favorite: fav });
                await onChange();
            } catch (e) { ui.showToast(`Favorite failed: ${e.message}`, 'error'); }
        });
    });
    el.querySelectorAll('.palace-del-chunk').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this memory?')) return;
            try {
                await palaceSend(`chunks/${btn.dataset.id}`, 'DELETE');
                ui.showToast('Deleted', 'success');
                await onChange();
            } catch (e) { ui.showToast(`Delete failed: ${e.message}`, 'error'); }
        });
    });
    el.querySelectorAll('.palace-unprune').forEach(btn => {
        btn.addEventListener('click', async () => {
            try {
                await palaceSend(`chunks/${btn.dataset.id}/unprune`, 'POST', {});
                ui.showToast('Restored to her recall', 'success');
                await onChange();
            } catch (e) { ui.showToast(`Restore failed: ${e.message}`, 'error'); }
        });
    });
}
